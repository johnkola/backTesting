import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import stylistic from '@stylistic/eslint-plugin'
import importX from 'eslint-plugin-import-x'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    plugins: {
      '@stylistic': stylistic,
      'import-x': importX,
    },
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // --- Formatting -------------------------------------------------------
      // These describe the style the codebase already had; they are here so
      // `npm run lint` catches a drift from it instead of leaving it to review.
      // Every one of them is auto-fixable: `npm run lint:fix`.
      '@stylistic/indent': ['error', 2, { SwitchCase: 1 }],
      '@stylistic/quotes': ['error', 'single', { avoidEscape: true }],
      '@stylistic/jsx-quotes': ['error', 'prefer-double'],
      '@stylistic/semi': ['error', 'never'],
      '@stylistic/comma-dangle': ['error', 'always-multiline'],
      '@stylistic/object-curly-spacing': ['error', 'always'],
      '@stylistic/arrow-parens': ['error', 'always'],
      '@stylistic/space-infix-ops': 'error',
      '@stylistic/keyword-spacing': 'error',
      '@stylistic/comma-spacing': 'error',

      // --- Stray whitespace -------------------------------------------------
      // One blank line separates things; two is an accident. Trailing spaces and
      // a missing final newline are invisible in review and noisy in diffs.
      '@stylistic/no-multiple-empty-lines': ['error', { max: 1, maxBOF: 0, maxEOF: 0 }],
      '@stylistic/no-trailing-spaces': 'error',
      '@stylistic/eol-last': ['error', 'always'],
      '@stylistic/padded-blocks': ['error', 'never'],

      // --- Imports ----------------------------------------------------------
      // Grouped and ordered so the top of a file reads the same everywhere:
      // packages first, then anything from this project. Unused and duplicate
      // imports are errors, not warnings — they are always a leftover.
      'import-x/order': ['error', {
        groups: [['builtin', 'external'], ['internal', 'parent', 'sibling', 'index']],
        'newlines-between': 'never',
      }],
      'import-x/no-duplicates': 'error',
      'import-x/first': 'error',
      'import-x/newline-after-import': ['error', { count: 1 }],
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
      }],
    },
  },
])
