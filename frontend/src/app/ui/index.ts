/**
 * The Offbeat UI library.
 *
 * Components here cover things with structure or behaviour. Anything that is just
 * "a native element with paint on it" — button, input, textarea, select, checkbox,
 * chip, divider — is a global class in src/styles/_components.scss instead, for
 * two reasons: native elements keep their built-in keyboard and form semantics,
 * and global CSS is not charged against the per-component style budget.
 *
 * Do not reintroduce Angular Material. It was removed deliberately; its rounded,
 * elevation-shadowed Material 3 language is the opposite of this one.
 */
export { ObIcon } from './icon/icon';
export type { IconName } from './icon/icon';
export { ObSpinner } from './spinner/spinner';
export { ObLoading, ObError, ObEmpty } from './state/state';
export { ObCard } from './card/card';
export type { CardTone } from './card/card';
export { ObField } from './field/field';
export { ObListRow } from './list-row/list-row';
export { ObPageHeader } from './page-header/page-header';
export { ObCallout } from './callout/callout';
export type { CalloutTone } from './callout/callout';
export { ObStat } from './stat/stat';
export type { StatTone } from './stat/stat';
export { ObTabs } from './tabs/tabs';
export { ObPaginator } from './paginator/paginator';
export { ObThemeToggle } from './theme-toggle/theme-toggle';
export { ToastService, ObToastHost } from './toast/toast';
export type { Toast, ToastTone } from './toast/toast';
export { ConfirmService, ObConfirmDialog } from './confirm/confirm';
export type { ConfirmOptions } from './confirm/confirm';
