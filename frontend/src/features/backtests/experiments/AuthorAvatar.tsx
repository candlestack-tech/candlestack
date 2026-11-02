import { Avatar } from '@/features/account';

// The initials of an author, the avatar the app draws for the user (Avatar.tsx), never a picture
// or the email: 16 px in the Compare panel and the charts' legends, 20 px in the compare head and
// on a backtest's header, 22 px on cards and in the table, 40 px in the author card
// (public-experiments, .avatar.xxs to .lg). The name shows in its title.
const SIZES = {
  xxs: 'size-4 text-[7.5px] tracking-[-0.03em]',
  xs: 'size-5 text-[9px] tracking-[-0.02em]',
  md: 'size-[22px] text-[9px]',
  lg: 'size-10 text-sm',
} as const;

export function AuthorAvatar({ name, size }: { name: string; size: keyof typeof SIZES }) {
  return (
    <span title={name} className="inline-flex flex-none">
      <Avatar user={{ display_name: name, email: '' }} className={SIZES[size]} />
    </span>
  );
}
