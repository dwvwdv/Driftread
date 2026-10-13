"""Paid-request tests use fake transports only; never contact TypeSafe."""
import asyncio
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.ai_runtime import ParsedResponse, ProviderResponse, RequestIdentity, RuntimeBlocked, execute_paid
from services.review_provider import build_request, evaluate_jev, parse_response


class ReceiptDB:
    def __init__(self, action='send'):
        self.action, self.calls, self.raw = action, [], None
        self.attempts = 0
        self.receipt = {'id': 'receipt', 'active_token': 'token', 'status': 'received', 'result': {'decision': 'accept'}}

    def rpc(self, name, params):
        self.calls.append((name, params))
        if name in {'reserve_ai_request', 'reserve_ai_retry'}:
            if self.action == 'send':
                self.attempts += 1
                self.receipt['attempts'] = self.attempts
            result = {'action': self.action, 'receipt_id': 'receipt', 'token': 'token', 'code': 'budget_exceeded', 'receipt': self.receipt, 'attempts': self.attempts}
        elif name == 'receive_ai_response':
            self.raw = params['p_raw']
            result = True
        elif name == 'finish_ai_request':
            self.receipt.update(status=params['p_status'], result=params.get('p_result'), error_code=params.get('p_error_code'))
            result = True
        else:
            raise AssertionError(name)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=result))

    def table(self, name):
        assert name == 'ai_request_attempts'
        return self

    def select(self, columns):
        assert columns == 'raw_response,http_status'
        return self

    def eq(self, name, value):
        assert (name, value) == ('token', 'token')
        return self

    def limit(self, value):
        assert value == 1
        return self

    def execute(self):
        return SimpleNamespace(data=[{'raw_response': self.raw, 'http_status': 200}])


IDENTITY = RequestIdentity('source_review', 'jev-1.13.0', 'input', 'policy', 'config')


@pytest.mark.asyncio
async def test_raw_commit_precedes_parse_and_completed_cache_avoids_payment():
    db, sends = ReceiptDB(), []
    async def send():
        sends.append(1)
        return ProviderResponse('raw')
    def parse(raw):
        assert db.raw == raw and db.calls[-1][0] == 'receive_ai_response'
        return ParsedResponse({'decision': 'accept'}, {'input_tokens': 1}, Decimal('.0001'), 'jev-1.13.0')
    kwargs = dict(reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=send, parse=parse)
    assert await execute_paid(db, IDENTITY, **kwargs) == {'decision': 'accept'}
    db.action = 'cached'
    assert await execute_paid(db, IDENTITY, **kwargs) == {'decision': 'accept'}
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_received_cache_recovers_after_crash_without_payment():
    db = ReceiptDB('cached')
    db.raw = 'durable'
    async def send():
        pytest.fail('cached receipt must not send')
    result = await execute_paid(db, IDENTITY, reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=send,
        parse=lambda raw: ParsedResponse({'decision': 'accept'}, {}, Decimal('0'), 'jev-1.13.0'))
    assert result['decision'] == 'accept'


@pytest.mark.asyncio
async def test_budget_block_happens_before_send():
    async def send():
        pytest.fail('no send before reservation')
    with pytest.raises(RuntimeBlocked, match='budget_exceeded'):
        await execute_paid(ReceiptDB('blocked'), IDENTITY, reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('0'), send=send, parse=lambda _: None)


@pytest.mark.asyncio
async def test_timeout_and_cancellation_record_unknown_without_leaking_error():
    for exception in [TimeoutError('secret token url source text'), asyncio.CancelledError()]:
        db = ReceiptDB()
        async def send():
            raise exception
        with pytest.raises((RuntimeBlocked, asyncio.CancelledError)) as exc:
            await execute_paid(db, IDENTITY, reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=send, parse=lambda _: None)
        assert 'secret' not in str(exc.value)
        assert db.receipt['status'] == 'unknown'


@pytest.mark.asyncio
async def test_invalid_provider_json_is_durable_and_never_repaid():
    db, sends = ReceiptDB(), []
    async def send():
        sends.append(1)
        return ProviderResponse('{bad secret text')
    def parse(raw):
        return json.loads(raw)
    kwargs = dict(reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=send, parse=parse)
    with pytest.raises(RuntimeBlocked, match='provider_invalid_response'):
        await execute_paid(db, IDENTITY, **kwargs)
    assert db.raw == '{bad secret text'
    db.action = 'cached'
    with pytest.raises(RuntimeBlocked):
        await execute_paid(db, IDENTITY, **kwargs)
    assert len(sends) == 1


def jev_response(quality=4, relevance=4, spam=.01):
    def score(value):
        return {'type': 'score', 'score': value, 'probabilities': {str(i): float(i == value) for i in range(5)}, 'confidence': 1}
    return json.dumps({'model': 'jev-1.13.0', 'answers': {'quality': score(quality), 'relevance': score(relevance), 'spam': {'type': 'noul', 'noul': spam}}, 'usage': {'input_tokens': 1000, 'output_tokens': 100}})


def test_jev_atomic_questions_normalization_and_cost():
    body, upper = build_request({'source': {'title': 'demo'}, 'samples': ['substantive'], 'lease_token': 'secret'}, {})
    assert set(body['questions']) == {'quality', 'relevance', 'spam'}
    assert 'secret' not in json.dumps(body)
    result = parse_response(jev_response(), {}).result
    assert result['decision'] == 'accept' and result['rationale'] is None
    assert result['dimensions'] == {'quality': 1, 'relevance': 1, 'spam_probability': .01}
    assert parse_response(jev_response(), {}).actual_usd == Decimal('.000042')
    assert upper > len(json.dumps(body))
    assert parse_response(jev_response(quality=1), {}).result['decision'] == 'reject'
    assert parse_response(jev_response(spam=.5), {}).result['decision'] == 'abstain'


@pytest.mark.parametrize('field,value', [('score', 9), ('score', float('nan')), ('confidence', True)])
def test_malformed_score_cannot_auto_pass(field, value):
    raw = json.loads(jev_response())
    raw['answers']['quality'][field] = value
    with pytest.raises(ValueError):
        parse_response(json.dumps(raw), {})


@pytest.mark.asyncio
async def test_jev_disabled_missing_key_and_unpriced_model_make_no_calls(monkeypatch):
    monkeypatch.delenv('JEV_API_KEY', raising=False)
    for settings, code in [({}, 'provider_disabled'), ({'enabled': True, 'engine': 'jev'}, 'provider_key_missing')]:
        with pytest.raises(RuntimeBlocked, match=code):
            await evaluate_jev(None, {}, settings)
    with pytest.raises(RuntimeBlocked, match='provider_model_unsupported'):
        build_request({}, {'model': 'unknown-model'})
    with pytest.raises(RuntimeBlocked, match='provider_context_limit'):
        build_request({'samples': ['一' * 30_000]}, {})


def test_request_identity_changes_with_policy_model_snapshot_config():
    for field in ['model', 'input_hash', 'policy_hash', 'config_hash']:
        values = dict(IDENTITY.__dict__)
        values[field] += '-new'
        assert RequestIdentity(**values).key != IDENTITY.key


def test_offline_evaluation_precision_recall_defer_and_id_validation():
    path = Path(__file__).parents[2] / 'scripts/source_review_eval.py'
    spec = importlib.util.spec_from_file_location('source_review_eval', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    labels = [{'id': 'good', 'label': 'accept'}, {'id': 'bad', 'label': 'reject'}]
    pred = [{'id': row['id'], 'confidence': .99, 'dimensions': {'quality': .9, 'relevance': .9, 'spam_probability': .01}} for row in labels]
    metrics = module.evaluate(labels, pred, {})
    assert metrics['precision'] == .5 and metrics['recall'] == 1 and metrics['false_accept_rate'] == 1
    pred[1]['confidence'] = .1
    metrics = module.evaluate(labels, pred, {})
    assert metrics['precision'] == 1 and metrics['defer_rate'] == .5
    with pytest.raises(ValueError, match='prediction_ids'):
        module.evaluate(labels, pred[:1], {})
    with pytest.raises(SystemExit):
        module.main(['--split', 'holdout', '--sweep-confidence', '.8', '.9'])


@pytest.mark.asyncio
async def test_expired_cache_is_not_repaid():
    db = ReceiptDB('cached')
    db.receipt.update(status='completed', result=None)
    async def send():
        pytest.fail('expired cache cannot silently repurchase')
    with pytest.raises(RuntimeBlocked, match='receipt_cache_expired'):
        await execute_paid(db, IDENTITY, reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=send, parse=lambda _: None)


def test_jev_cjk_samples_keep_evidence_and_fit_conservative_context():
    samples = [{'evidence_id': f'rss:{i}', 'title': '標題' * 50, 'text': '中文' * 1000} for i in range(10)]
    body, upper = build_request({'samples': samples, 'policy': {'policy_text': '科技研究與時事', 'daily_budget_usd': 1}}, {})
    assert len(body['state']['samples']) == 10 and upper <= 30_000
    assert body['state']['policy'] == '科技研究與時事'
    assert all(row['evidence_id'] == f'rss:{i}' for i, row in enumerate(body['state']['samples']))
    assert samples[0]['text'] == '中文' * 1000  # don't mutate durable packet


def test_jev_confidence_is_bounded_without_formula_hard_rejection():
    raw = json.loads(jev_response())
    raw['answers']['quality']['probabilities'] = {str(i): .2 for i in range(5)}
    raw['answers']['quality']['score'] = 2
    raw['answers']['quality']['confidence'] = .5
    assert parse_response(json.dumps(raw), {}).result['decision'] == 'abstain'
    raw['answers']['quality']['probabilities']['0'] = .8
    with pytest.raises(ValueError, match='invalid_distribution'):
        parse_response(json.dumps(raw), {})


@pytest.mark.asyncio
@pytest.mark.parametrize('first_outcome', ['unknown', 429, 503])
async def test_transient_outcome_automatically_retries_once_and_recovers(first_outcome):
    db, sends = ReceiptDB(), []
    async def send():
        sends.append(1)
        if len(sends) == 1:
            if first_outcome == 'unknown':
                raise TimeoutError('secret provider details')
            return ProviderResponse('temporary provider body', first_outcome)
        return ProviderResponse('good')
    result = await execute_paid(db, IDENTITY, reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=send,
        parse=lambda _: ParsedResponse({'decision': 'accept'}, {}, Decimal('.0001'), 'jev-1.13.0'))
    assert len(sends) == 2 and db.attempts == 2 and result['decision'] == 'accept'


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome,code', [('unknown', 'request_unknown_paused'), (503, 'provider_http_retryable'), (401, 'provider_http_error')])
async def test_automatic_retry_has_a_hard_two_send_bound(outcome, code):
    db, sends = ReceiptDB(), []
    async def send():
        sends.append(1)
        if outcome == 'unknown':
            raise TimeoutError()
        return ProviderResponse('error', outcome)
    with pytest.raises(RuntimeBlocked, match=code):
        await execute_paid(db, IDENTITY, reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=send, parse=lambda _: None)
    assert len(sends) == (1 if outcome == 401 else 2)


@pytest.mark.asyncio
async def test_retry_budget_block_does_not_send_and_cancellation_never_retries():
    db, sends = ReceiptDB(), []
    async def send():
        sends.append(1)
        db.action = 'blocked'
        raise TimeoutError()
    with pytest.raises(RuntimeBlocked, match='budget_exceeded'):
        await execute_paid(db, IDENTITY, reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=send, parse=lambda _: None)
    assert len(sends) == 1
    db, sends = ReceiptDB(), []
    async def cancelled():
        sends.append(1)
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await execute_paid(db, IDENTITY, reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=cancelled, parse=lambda _: None)
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_optional_receipt_metadata_survives_send_and_cached_result():
    db = ReceiptDB()
    async def send():
        return ProviderResponse('good')
    kwargs = dict(reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'), send=send,
        parse=lambda _: ParsedResponse({'decision': 'accept'}, {}, Decimal('.0001'), 'jev-1.13.0'), return_metadata=True)
    result = await execute_paid(db, IDENTITY, **kwargs)
    assert result == {'result': {'decision': 'accept'}, 'receipt_id': 'receipt'}
    db.action = 'cached'
    assert await execute_paid(db, IDENTITY, **kwargs) == result


@pytest.mark.asyncio
async def test_jev_analysis_links_receipt_and_actual_provider_input(monkeypatch):
    from services import review_provider
    from services.ai_runtime import digest
    db = ReceiptDB()
    monkeypatch.setenv('JEV_API_KEY', 'fake-never-sent-to-provider')
    async def send(body, key):
        assert key == 'fake-never-sent-to-provider'
        return ProviderResponse(jev_response())
    monkeypatch.setattr(review_provider, '_send_request', send)
    packet = {'source': {'title': 'sample'}, 'samples': [{'evidence_id': 'rss:0', 'text': 'x' * 2000}],
        'policy': {'policy_text': 'Research'}, 'rubric_hash': 'a' * 64}
    settings = {'enabled': True, 'engine': 'jev', 'daily_budget_usd': 1}
    result = await evaluate_jev(db, packet, settings)
    metadata = result['_analysis_metadata']
    body, _ = build_request(packet, settings)
    assert metadata['receipt_id'] == 'receipt'
    assert metadata['provider_input_hash'] == digest(body['state'])
    assert metadata['provider_prompt_hash'] == digest({'questions': body['questions'], 'policy': body['state']['policy']})
    assert metadata['rubric_hash'] == 'a' * 64
    assert metadata['adapter_version'] == review_provider.ADAPTER_VERSION
    assert 'fake-never-sent' not in json.dumps(metadata)
    db.action = 'cached'
    assert (await evaluate_jev(db, packet, settings))['_analysis_metadata'] == metadata


@pytest.mark.asyncio
async def test_explicit_retry_key_is_preserved_across_automatic_transport_retry():
    db, sends = ReceiptDB(), []
    async def send():
        sends.append(1)
        if len(sends) == 1:
            raise TimeoutError()
        return ProviderResponse('good')
    result = await execute_paid(db, IDENTITY, reserved_usd=Decimal('.001'), daily_budget_usd=Decimal('1'),
        send=send, parse=lambda _: ParsedResponse({'decision': 'accept'}, {}, Decimal('0'), 'jev-1.13.0'),
        explicit_retry_key='00000000-0000-0000-0000-000000000001')
    assert result['decision'] == 'accept' and len(sends) == 2
    reservations = [(name, params) for name, params in db.calls if name.startswith('reserve_')]
    assert len(reservations) == 2
    assert all(name == 'reserve_ai_retry' and params['p_retry_key'].endswith('1') for name, params in reservations)


@pytest.mark.asyncio
async def test_jev_uses_only_server_task_retry_key(monkeypatch):
    from services import review_provider
    db = ReceiptDB()
    monkeypatch.setenv('JEV_API_KEY', 'fake')
    async def send(body, key):
        return ProviderResponse(jev_response())
    monkeypatch.setattr(review_provider, '_send_request', send)
    await evaluate_jev(db, {'paid_retry_key': '00000000-0000-0000-0000-000000000002'},
        {'enabled': True, 'engine': 'jev', 'daily_budget_usd': 1})
    assert db.calls[0][0] == 'reserve_ai_retry'
    assert db.calls[0][1]['p_retry_key'].endswith('2')
