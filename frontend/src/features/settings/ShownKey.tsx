import { useState, type ReactNode } from 'react';

import type { ApiKeyCreated } from '@/api';
import { ClaudeLogo, CursorLogo } from '@/components/clientLogos';
import { PrimaryButton } from '@/components/DialogButtons';
import { DialogBody, DialogClose, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/utils';

import { exampleText, newKeySummary, type Example, type Expiry } from './apiKeyText';
import { Field, SEGMENT } from './keyFields';
import { CopyButton, GuideLink, WarnNote } from './keyParts';

const EXAMPLES: { value: Example; label: string; logo?: ReactNode }[] = [
  { value: 'api', label: 'API' },
  { value: 'claude-code', label: 'Claude Code', logo: <ClaudeLogo /> },
  { value: 'cursor', label: 'Cursor', logo: <CursorLogo /> },
];

// .snip: the example with its comment lines muted, and Copy in its corner.
function ExampleCode({ text }: { text: string }) {
  return (
    <div className="relative rounded-lg border bg-muted">
      <pre className="m-0 py-3 pr-11 pl-3 font-mono text-[12.5px]/[1.6] wrap-anywhere whitespace-pre-wrap">
        {text.split('\n').map((line, index) => (
          <span key={index} className={cn(line.startsWith('#') && 'text-muted-foreground')}>
            {index > 0 && '\n'}
            {line}
          </span>
        ))}
      </pre>
      <span className="absolute top-2 right-2">
        <CopyButton text={text} label="Copy the example" />
      </span>
    </div>
  );
}

// Try it: the API, Claude Code and Cursor, each with the new key filled in.
function TryIt({ secret }: { secret: string }) {
  const [example, setExample] = useState<Example>('api');
  return (
    <Field label="Try it">
      <ToggleGroup aria-label="Try it" value={example} onValueChange={setExample}>
        {EXAMPLES.map(({ value, label, logo }) => (
          <ToggleGroupItem key={value} value={value} className={SEGMENT}>
            {logo}
            {label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <ExampleCode text={exampleText(example, secret, window.location.origin)} />
    </Field>
  );
}

// The key, once (design/mockups/api-mcp, MODALS.shown): the amber warning, the key with Copy, and
// the examples. Copying is not needed to close it.
export function ShownKey({ apiKey, expiry }: { apiKey: ApiKeyCreated; expiry: Expiry }) {
  return (
    <>
      <DialogBody className="gap-4">
        <WarnNote>
          <span className="flex min-w-0 flex-col gap-0.5">
            <b className="font-semibold">You will not see it again</b>
            <DialogDescription className="text-[13px] text-foreground">
              Store it in a password manager or your app&apos;s secrets. Anyone with it can act as you.
            </DialogDescription>
          </span>
        </WarnNote>
        <Field label={apiKey.name} side={newKeySummary(apiKey.scopes, expiry)}>
          <div className="flex items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--accent)_45%,var(--rule))] bg-accent-soft py-2.5 pr-2.5 pl-3">
            <code className="min-w-0 flex-1 font-mono text-[13.5px]/[1.45] font-medium wrap-anywhere">
              {apiKey.secret}
            </code>
            <CopyButton text={apiKey.secret} />
          </div>
        </Field>
        <TryIt secret={apiKey.secret} />
      </DialogBody>
      <DialogFooter>
        <GuideLink className="mr-auto" />
        <DialogClose asChild>
          <PrimaryButton>Done</PrimaryButton>
        </DialogClose>
      </DialogFooter>
    </>
  );
}
