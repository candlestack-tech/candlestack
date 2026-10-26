import { useSuspenseQuery } from '@tanstack/react-query';

import {
  preferencesQueryOptions,
  type Appearance,
  type DateFormat,
  type Theme,
  type TimeFormat,
  type TimeZone,
} from '@/api';
import { DashboardCard } from '@/components/DashboardCard';
import { ErrorNote } from '@/components/ErrorNote';
import { AppearanceIcon } from '@/components/icons';
import { Switch } from '@/components/ui/switch';
import { useAppearanceSave, useThemeSetting, useUpdateAppearance } from '@/features/preferences';
import { cityOf, formatDate, formatTime, viewerTimeZone } from '@/lib/dates';

import { Choice } from './controls';
import { SettingsRow } from './rows';
import { SaveIndicator } from './SaveIndicator';

// The formats show themselves on one moment: 12 October 2024, 16:05.
const EXAMPLE = new Date(Date.UTC(2024, 9, 12, 16, 5));
const DATE_FORMATS: readonly DateFormat[] = ['dmy', 'iso', 'dot'];
const TIME_FORMATS: readonly TimeFormat[] = ['24', '12'];

// The theme on the page, which the header's toggle changes too.
function ThemeRow() {
  const { theme, setTheme } = useThemeSetting();
  return (
    <SettingsRow label="Theme">
      <Choice<Theme>
        label="Theme"
        value={theme}
        options={[
          ['light', 'Light'],
          ['dark', 'Dark'],
        ]}
        onChange={setTheme}
      />
    </SettingsRow>
  );
}

type RowsProps = { appearance: Appearance; update: (change: Partial<Appearance>) => void };

// The clock and how dates and times are written, each format shown on the same moment.
function TimeRows({ appearance, update }: RowsProps) {
  return (
    <>
      <SettingsRow label="Time zone" hint={appearance.time_zone === 'ny' ? 'Exchange time' : 'Your time zone'}>
        <Choice<TimeZone>
          label="Time zone"
          value={appearance.time_zone}
          options={[
            ['ny', 'New York'],
            ['local', cityOf(viewerTimeZone())],
          ]}
          onChange={(time_zone) => update({ time_zone })}
        />
      </SettingsRow>
      <SettingsRow label="Date format">
        <Choice<DateFormat>
          label="Date format"
          value={appearance.date_format}
          options={DATE_FORMATS.map((format) => [format, formatDate(EXAMPLE, { timeZone: 'UTC', dateFormat: format })])}
          onChange={(date_format) => update({ date_format })}
        />
      </SettingsRow>
      <SettingsRow label="Time format">
        <Choice<TimeFormat>
          label="Time format"
          value={appearance.time_format}
          options={TIME_FORMATS.map((format) => [format, formatTime(EXAMPLE, { timeZone: 'UTC', timeFormat: format })])}
          onChange={(time_format) => update({ time_format })}
        />
      </SettingsRow>
    </>
  );
}

// How the app looks for this account; each control saves as it changes, the indicator by the
// title says how that went, and a failed change goes back and says why (Retry makes it again).
export function AppearanceSection() {
  const { appearance } = useSuspenseQuery(preferencesQueryOptions()).data;
  const { mutate } = useUpdateAppearance();
  const { status, error, retry } = useAppearanceSave();
  return (
    <DashboardCard icon={AppearanceIcon} title="Appearance" aside={<SaveIndicator state={{ status, retry }} />}>
      <ThemeRow />
      <TimeRows appearance={appearance} update={mutate} />
      <SettingsRow label="Reduce motion" hint="Turns off the small animations">
        <Switch
          aria-label="Reduce motion"
          checked={appearance.reduce_motion}
          onCheckedChange={(reduce_motion) => mutate({ reduce_motion })}
        />
      </SettingsRow>
      {error && <ErrorNote className="mt-2">{error.message}</ErrorNote>}
    </DashboardCard>
  );
}
