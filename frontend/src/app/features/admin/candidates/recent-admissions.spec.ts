import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { RecentAdmission } from '../../../models';
import { AdminService } from '../../../services/admin';
import { RecentAdmissions } from './recent-admissions';

const admission = (method: RecentAdmission['method'] = 'manual'): RecentAdmission => ({
  feed_id: method,
  title: `來源 ${method}`,
  feed_url: `https://example.org/${method}/feed`,
  website_url: 'https://example.org',
  admitted_at: '2026-10-16T08:30:00Z',
  method,
  engine: null,
});

function setup(read = () => of({ items: [] as RecentAdmission[], limit: 10 })) {
  const limits: number[] = [];
  TestBed.configureTestingModule({
    imports: [RecentAdmissions],
    providers: [
      {
        provide: AdminService,
        useValue: {
          recentAdmissions: (limit: number) => {
            limits.push(limit);
            return read();
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(RecentAdmissions);
  fixture.componentRef.setInput('refreshVersion', 0);
  fixture.detectChanges();
  const button = (text: string) =>
    Array.from(
      fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>,
    ).find((item) => item.textContent?.trim() === text)!;
  return { fixture, limits, button };
}

describe('RecentAdmissions', () => {
  it('loads ten read-only records and renders each actual admission method and AI engine', () => {
    const methods: RecentAdmission['method'][] = [
      'ai_auto',
      'human_agree',
      'human_disagree',
      'manual',
      'referrer_auto',
      'unknown',
    ];
    const { fixture, limits } = setup(() =>
      of({
        items: methods.map((method, index) => ({
          ...admission(method),
          engine: index === 0 ? ('external_agent' as const) : index === 1 ? ('jev' as const) : null,
        })),
        limit: 10,
      }),
    );
    const element = fixture.nativeElement as HTMLElement;
    expect(limits).toEqual([10]);
    expect(element.querySelectorAll('li')).toHaveLength(6);
    for (const label of [
      'AI 自動收錄',
      '人工同意 AI 後收錄',
      '人工推翻 AI 後收錄',
      '人工直接收錄',
      '排程自動收錄',
      '歷史／其他管道（未記錄）',
      'MCP · 外部 AI',
      'Jev · AI 評審',
    ]) {
      expect(element.textContent).toContain(label);
    }
    expect(element.textContent).toContain('https://example.org/ai_auto/feed');
    expect(element.querySelector('time')?.getAttribute('datetime')).toBe('2026-10-16T08:30:00Z');
    expect(element.querySelector('a')?.getAttribute('href')).toBe('/manage/feeds');
    expect(element.querySelectorAll('button')).toHaveLength(2);
  });

  it('shows an independent empty state and collapses without refetching', () => {
    const { fixture, button, limits } = setup();
    expect(fixture.nativeElement.textContent).toContain('尚無已收錄的來源');
    button('收合').click();
    fixture.detectChanges();
    expect(button('展開').getAttribute('aria-expanded')).toBe('false');
    expect(fixture.nativeElement.querySelector('#recent-admissions-content').hidden).toBe(true);
    button('展開').click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('#recent-admissions-content').hidden).toBe(false);
    expect(limits).toEqual([10]);
    button('重新整理近期收錄').click();
    expect(limits).toEqual([10, 10]);
  });

  it('keeps existing rows on refresh failure and permits retrying this section', () => {
    const response = new Subject<{ items: RecentAdmission[]; limit: number }>();
    let calls = 0;
    const { fixture, button } = setup(() =>
      ++calls === 1 ? of({ items: [admission()], limit: 10 }) : response,
    );
    button('重新整理近期收錄').click();
    fixture.detectChanges();
    expect(button('重新整理近期收錄').disabled).toBe(true);
    expect(fixture.nativeElement.textContent).toContain('正在更新近期收錄');
    response.error(new Error('secret backend detail'));
    fixture.detectChanges();
    expect(button('重新整理近期收錄').disabled).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('近期收錄暫時無法讀取');
    expect(fixture.nativeElement.textContent).toContain('以下保留上次讀取的紀錄');
    expect(fixture.nativeElement.textContent).toContain('來源 manual');
    expect(fixture.nativeElement.textContent).not.toContain('secret backend detail');
  });

  it('cancels superseded reads and refreshes once when the admission version changes', () => {
    const old = new Subject<{ items: RecentAdmission[]; limit: number }>();
    const latest = new Subject<{ items: RecentAdmission[]; limit: number }>();
    let calls = 0;
    const { fixture, limits } = setup(() => (++calls === 1 ? old : latest));
    expect(fixture.nativeElement.textContent).toContain('讀取近期收錄中');
    fixture.componentRef.setInput('refreshVersion', 1);
    fixture.detectChanges();
    expect(old.observed).toBe(false);
    latest.next({ items: [admission('ai_auto')], limit: 10 });
    old.next({ items: [admission('manual')], limit: 10 });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('來源 ai_auto');
    expect(fixture.nativeElement.textContent).not.toContain('來源 manual');
    expect(limits).toEqual([10, 10]);
    fixture.destroy();
    expect(latest.observed).toBe(false);
  });

  it('renders untrusted titles as text', () => {
    const { fixture } = setup(() =>
      of({ items: [{ ...admission(), title: '<img src=x onerror=alert(1)>' }], limit: 10 }),
    );
    expect(fixture.nativeElement.querySelector('img')).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('<img src=x onerror=alert(1)>');
  });
});
