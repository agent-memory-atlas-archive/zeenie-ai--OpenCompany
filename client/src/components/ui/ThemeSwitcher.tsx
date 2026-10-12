/**
 * ThemeSwitcher — picks the active visual theme.
 *
 * Renders one DropdownMenu item per theme listed in `AVAILABLE_THEMES`,
 * in that order.
 *
 * Adding a new theme: drop a CSS file under client/src/themes/, import
 * it in main.tsx, add the name to `AVAILABLE_THEMES` in ThemeContext,
 * and add an entry to `THEME_META` below.
 */

import * as React from 'react';
import { Check, Palette } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { AVAILABLE_THEMES, useTheme, type ThemeName } from '../../contexts/ThemeContext';

interface ThemeMeta {
  label: string;
  blurb: string;
}

const THEME_META: Record<ThemeName, ThemeMeta> = {
  light:  { label: 'Light',         blurb: 'Clean default' },
  dark:   { label: 'Dark',          blurb: 'Solarized + Dracula' },
  atomic: { label: 'Atomic Modern', blurb: 'Eames mid-century' },
};

export const ThemeSwitcher: React.FC<{ className?: string }> = ({ className }) => {
  const { theme, setTheme } = useTheme();
  const activeMeta = THEME_META[theme];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="icon-sm"
          title={`Theme: ${activeMeta.label}`}
          className="border-action-secret-border bg-action-secret-soft text-action-secret-ink hover:bg-action-secret-hover"
        >
          <Palette />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className={className ?? 'min-w-[240px]'}>
        {AVAILABLE_THEMES.map((name) => {
          const meta = THEME_META[name];
          const isActive = name === theme;
          return (
            <DropdownMenuItem
              key={name}
              onSelect={() => setTheme(name)}
              className="flex items-center gap-2"
            >
              <span className="flex h-4 w-4 items-center justify-center">
                {isActive && <Check className="h-3.5 w-3.5" />}
              </span>
              <span className="flex-1">
                <span className="block text-sm font-medium">{meta.label}</span>
                <span className="block text-[11px] text-muted-foreground">{meta.blurb}</span>
              </span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export default ThemeSwitcher;
