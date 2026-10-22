import { ToastService } from '../ui/toast/toast';
import { OfflineReadingService } from './offline-reading';
import { OfflineView } from './offline-state';
import { OfflineKind } from './offline-store';

/** Shared by the list and the single-article page. */
export async function toggleOffline(
  offline: OfflineReadingService,
  toast: ToastService,
  view: OfflineView,
  kind: OfflineKind,
): Promise<void> {
  const current =
    kind === 'read' ? view.read : kind === 'favorite' ? view.favorite : view.readLater;
  const result = await offline.setState(view.article.id, kind, !current);
  if (!result.queued) {
    toast.danger(offline.error() ?? '無法排入離線操作');
    return;
  }
  if (!result.replay) {
    toast.info('已排入待同步，重新連線後送出');
    return;
  }
  const replay = await result.replay;
  // 404/409: the article left the user's access; the snapshot state is back.
  if (replay.dropped > 0) toast.warning('文章已無法存取，離線操作已取消並還原');
}
