/** @type {import('stylelint').Config} */
export default {
  // See https://github.com/wagtail/stylelint-config-wagtail for rules.
  extends: ['@wagtail/stylelint-config-wagtail'],
  rules: {
    // Aspect-ratio boxes and fluid percentage offsets can’t come from the spacing scale.
    'scale-unlimited/declaration-strict-value': [
      [
        'color',
        '/-color/',
        'fill',
        'stroke',
        'font-family',
        'font-size',
        'font-weight',
        '/margin/',
        '/padding/',
        'gap',
        'z-index',
      ],
      {
        ignoreValues: [
          'auto',
          'currentColor',
          'inherit',
          'initial',
          'none',
          'unset',
          'transparent',
          'Canvas',
          'CanvasText',
          'LinkText',
          'VisitedText',
          'ActiveText',
          'ButtonFace',
          'ButtonText',
          'ButtonBorder',
          'Field',
          'FieldText',
          'Highlight',
          'HighlightText',
          'SelectedItem',
          'SelectedItemText',
          'Mark',
          'MarkText',
          'GrayText',
          'AccentColor',
          'AccentColorText',
          // Percentage values: used for aspect-ratio boxes and fluid grid offsets.
          '/^\\d*\\.?\\d+%$/',
          // Negative percentage fluid offsets, e.g. -4%.
          '/^-\\d*\\.?\\d+%$/',
        ],
      },
    ],
    // Rules which we ideally would want to enforce but are reporting too many
    // issues in the legacy stylesheet currently. Re-enable progressively.
    'declaration-no-important': null,
    'scss/at-extend-no-missing-placeholder': null,
    'scss/selector-class-pattern': null,
    // The entry stylesheet imports partials mid-file so the cascade follows the
    // documented section order (variables, elements, modules, utilities).
    'no-invalid-position-at-import-rule': null,
  },
};
