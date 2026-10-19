import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  // Ignore build output
  { ignores: ['dist', 'node_modules'] },

  // Base JS recommended rules
  js.configs.recommended,

  // TypeScript files
  {
    files: ['**/*.{ts,tsx}'],
    extends: [...tseslint.configs.recommended],
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // React Hooks — adopt the v7 recommended (React Compiler) rule set.
      // Real-bug rules (purity, static-components, set-state-in-render, etc.)
      // stay errors and are fixed. Every historically-downgraded rule has now
      // been driven to error as its sites were cleaned up.
      //
      // exhaustive-deps is now an error: the last two flagged sites were the
      // same shape — an SWR-derived array (`data?.items ?? []` in ReleasesPage,
      // `users ?? []` in TestManagementPage) recreated as a fresh literal every
      // render and then used as a useMemo dependency, defeating the downstream
      // memo. Each was wrapped in its own useMemo (the fix the rule itself
      // recommends), matching the `projectMembers = useMemo(() => … ?? [], […])`
      // pattern already in those files.
      //
      // set-state-in-effect is now an error: every flagged site was migrated
      // off load/reset-state-in-effect — pagination/selection resets use the
      // adjust-state-during-render previous-value pattern, data fetches moved to
      // SWR hooks, and the two genuine cases that must stay effects (a network
      // token re-verification in ProtectedRoute, a coordinated one-time
      // deep-link expand+scroll in TestManagementPage) carry scoped disables.
      //
      // immutability is now an error: the only flagged cases were a
      // wrapper-hook ref not named with the "Ref" suffix the rule keys on,
      // and a self-referencing reconnect timer — both fixed by routing the
      // reconnect through a connectRef and renaming the ref.
      //
      // refs is now an error: every flagged case was a single component
      // (CasesFilterBar) that took its props as an undestructured `p` object
      // whose `searchInputRef` member made the rule treat *all* `p.*` reads as
      // ref-reads-during-render. Destructuring the props at the parameter keeps
      // the ref a named binding (forwarded to a DOM `ref=`, which is allowed)
      // and clears every false positive.
      ...reactHooks.configs['recommended-latest'].rules,
      'react-hooks/set-state-in-effect': 'error',
      'react-hooks/refs': 'error',
      'react-hooks/immutability': 'error',
      'react-hooks/exhaustive-deps': 'error',

      // React Refresh (Vite HMR)
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // TypeScript — relax rules that block rapid iteration
      //
      // no-explicit-any is now an error: every production site had already been
      // given a real type, leaving one last source case — useTableSort's generic
      // constraint `T extends Record<string, any>`, which only needs an indexable
      // shape. Its sort body already narrows with runtime `typeof` checks and a
      // `String()` fallback, so `Record<string, unknown>` types it precisely.
      // The single remaining `any` is a test-only `File.prototype` mock that is
      // genuinely unavoidable and carries a scoped disable. Promoting the rule to
      // error guards against reintroducing untyped `any` in app code.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
      }],
      // no-non-null-assertion is now an error: every `x!` non-null assertion in
      // src/ was replaced with a real guard, early-return, default, or narrowing
      // as its owning page/area was cleaned up over successive ratchet passes,
      // leaving zero remaining sites. Promoting the rule to error guards against
      // reintroducing unchecked `!` assertions — which silence the compiler's
      // null/undefined analysis and turn a would-be type error into a runtime
      // crash — in app code.
      '@typescript-eslint/no-non-null-assertion': 'error',

      // Design rule: every color comes from a theme token. Raw Tailwind palette classes
      // (text-emerald-400, bg-red-900/40, …) bypass the per-theme token
      // system (--status-*, --gate-*, --color-*) and are each a light-theme
      // defect.
      //
      // no-restricted-syntax is now an error, closing the final warn-level
      // ratchet: every raw palette class in src/ was mapped by semantic role to
      // a per-theme token (--status-*/--gate-*/--color-*, defined in
      // src/index.css) over successive page-by-page passes, leaving zero UI
      // sites. The only remaining literal matches are assertion guards in two
      // test files (RightRail/ReleaseHealth) that name the raw classes to prove
      // they are ABSENT — not UI — and carry scoped disables, mirroring the
      // avatar-color-swatch exemption. Promoting the rule to error guards
      // against reintroducing token-bypassing palette classes in app code.
      // Avatar-color swatches (user data, not theme UI) are exempt by intent.
      //
      // W29a UIX-13 widened it by APPENDED selectors (the first object stays
      // byte-identical: hooks/paletteRatchet.promotion.test.ts pins it): every
      // Tailwind hue on every colour utility, and white / black on text / bg /
      // border / ring, each in a string Literal AND in a template literal's
      // text (class strings built with `${…}` escaped the old rule). A
      // legitimate white / black (a toggle knob, a QR code's quiet zone, a
      // scrim) carries `// eslint-disable-next-line no-restricted-syntax --
      // <reason>`. A test asserting a class is ABSENT uses a regex literal
      // (/text-white/), which these selectors do not read.
      'no-restricted-syntax': ['error', {
        selector: 'Literal[value=/(text|bg|border)-(emerald|green|red|amber|yellow|orange|purple|blue)-[0-9]{2,3}/]',
        message: 'Use theme tokens (--status-*, --gate-*, --color-*) instead of raw palette classes.',
      }, {
        selector: 'Literal[value=/(?<![\\w-])(text|bg|border|ring|fill|stroke|from|via|to|accent|divide|outline|decoration|placeholder|caret|shadow)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}(?![\\w-])/]',
        message: 'A raw Tailwind palette colour ignores the theme. Use a token: text-[var(--color-text…)], bg-[var(--color-bg…)], border-[var(--color-border…)], a result hue var(--status-*), a meaning var(--ui-danger|warning|success|info…).',
      }, {
        selector: 'TemplateElement[value.raw=/(?<![\\w-])(text|bg|border|ring|fill|stroke|from|via|to|accent|divide|outline|decoration|placeholder|caret|shadow)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}(?![\\w-])/]',
        message: 'A raw Tailwind palette colour ignores the theme. Use a token: text-[var(--color-text…)], bg-[var(--color-bg…)], border-[var(--color-border…)], a result hue var(--status-*), a meaning var(--ui-danger|warning|success|info…).',
      }, {
        selector: 'Literal[value=/(?<![\\w-])(text|bg|border|ring)-(white|black)(?![\\w-])/]',
        message: 'White / black ignore the theme (white is invisible on the light themes, black on the dark ones). Use a token: text-[var(--color-text)], text-[var(--color-btn-primary-text)] on a primary fill, text-[var(--color-text-on-status)] on a solid status fill, bg-[var(--color-bg-hover)] for a sheen; a legitimate knob / scrim / QR zone carries a disable comment with its reason.',
      }, {
        selector: 'TemplateElement[value.raw=/(?<![\\w-])(text|bg|border|ring)-(white|black)(?![\\w-])/]',
        message: 'White / black ignore the theme (white is invisible on the light themes, black on the dark ones). Use a token: text-[var(--color-text)], text-[var(--color-btn-primary-text)] on a primary fill, text-[var(--color-text-on-status)] on a solid status fill, bg-[var(--color-bg-hover)] for a sheen; a legitimate knob / scrim / QR zone carries a disable comment with its reason.',
      }],

      // Allow console in development
      'no-console': 'off',
    },
  },

  // App code: the `swr` module's own `mutate` is bound to SWR's default cache,
  // and main.tsx renders every key into a provider Map, so it matches nothing
  // and resolves having done nothing. Ten refresh-after-write helpers shipped
  // on it (Integration Health's "Probe All Now" never showed its results, the
  // suites list never refreshed after a create) — the browser E2E pass,
  // 2026-10-07. Tests may mock `swr` and stay exempt.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/**/*.test.{ts,tsx}', 'src/test/**'],
    rules: {
      'no-restricted-imports': ['error', {
        paths: [{
          name: 'swr',
          importNames: ['mutate'],
          message: "A no-op here (SWR's default cache, not the app's provider). Use appMutate from '@/utils/swrCacheMutate', or the mutate useSWR / useSWRConfig returns.",
        }],
      }],
    },
  },

  // W29a UIX-10: ONE date and number format. Pages and components format
  // through utils/formatters (a FIXED en-US locale, like the numbers: a
  // screenshot, a CSV and a pasted link never change with the machine), never
  // with the browser's toLocale*String / Intl. The chart kit is W29b's (UIX-05)
  // and keeps its own; utils/ is where the formatters live. A different rule
  // from the palette one, so no options array is repeated.
  {
    files: ['src/pages/**/*.{ts,tsx}', 'src/components/**/*.{ts,tsx}'],
    ignores: ['src/components/charts/**', 'src/**/*.test.{ts,tsx}'],
    rules: {
      'no-restricted-properties': ['error', {
        property: 'toLocaleDateString',
        message: "Use formatDay / formatMonthDay / formatWeekday from '@/utils/formatters' ('Sep 18, 2026' / 'Sep 18' / 'Friday').",
      }, {
        property: 'toLocaleTimeString',
        message: "Use formatClock from '@/utils/formatters' ('10:05:33', 24 h).",
      }, {
        property: 'toLocaleString',
        message: "A date + time: formatCompactDateTime ('Sep 18, 11:50'); a number: formatNumber — both from '@/utils/formatters'.",
      }, {
        object: 'Intl',
        property: 'NumberFormat',
        message: "Use formatNumber / formatPercent / formatCompact from '@/utils/formatters' (one fixed locale for every number).",
      }, {
        object: 'Intl',
        property: 'DateTimeFormat',
        message: "Use formatDay / formatMonthDay / formatWeekday / formatClock / formatCompactDateTime from '@/utils/formatters'.",
      }],
    },
  },

  // Plain JS/TS config files (vite, tailwind, postcss)
  {
    files: ['*.config.{js,ts,mjs,cjs}', '*.config.*.{js,ts}'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
)
