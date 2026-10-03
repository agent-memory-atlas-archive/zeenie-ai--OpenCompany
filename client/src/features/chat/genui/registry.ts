/**
 * The chat's generated UI for json-render: the catalogue (catalog.ts's
 * CHAT_CATALOG) and its registry, a guarded component for each of the
 * twelve types (views.tsx drawn through lib/jsonRender/guard.tsx, which reads
 * each element's props through the catalogue's forgiving schema first and
 * holds it to the parents it may appear under).
 *
 * Defined here, not in catalog.ts, because this module loads only with a
 * generated UI (ChatUi), and json-render with it.
 */

import { defineCatalog } from '@json-render/core';
import { defineRegistry, schema } from '@json-render/react';
import { guarded } from '@/lib/jsonRender';
import { CHAT_CATALOG, CHAT_PROP_SCHEMAS, allowedParents } from './catalog';
import {
  BarChartView,
  ButtonView,
  CalloutView,
  CardView,
  RowView,
  SelectView,
  SlotPickerView,
  StackView,
  StatGridView,
  TextFieldView,
  TextView,
  ToggleView,
} from './views';

export const chatCatalog = defineCatalog(schema, CHAT_CATALOG);

export const { registry: chatRegistry } = defineRegistry(chatCatalog, {
  components: {
    Stack: guarded('Stack', CHAT_PROP_SCHEMAS.Stack, StackView, { parents: allowedParents('Stack') }),
    Row: guarded('Row', CHAT_PROP_SCHEMAS.Row, RowView, { parents: allowedParents('Row') }),
    Card: guarded('Card', CHAT_PROP_SCHEMAS.Card, CardView, { parents: allowedParents('Card') }),
    SlotPicker: guarded('SlotPicker', CHAT_PROP_SCHEMAS.SlotPicker, SlotPickerView, { parents: allowedParents('SlotPicker') }),
    Select: guarded('Select', CHAT_PROP_SCHEMAS.Select, SelectView, { parents: allowedParents('Select') }),
    Toggle: guarded('Toggle', CHAT_PROP_SCHEMAS.Toggle, ToggleView, { parents: allowedParents('Toggle') }),
    TextField: guarded('TextField', CHAT_PROP_SCHEMAS.TextField, TextFieldView, { parents: allowedParents('TextField') }),
    Text: guarded('Text', CHAT_PROP_SCHEMAS.Text, TextView, { parents: allowedParents('Text') }),
    StatGrid: guarded('StatGrid', CHAT_PROP_SCHEMAS.StatGrid, StatGridView, { parents: allowedParents('StatGrid') }),
    BarChart: guarded('BarChart', CHAT_PROP_SCHEMAS.BarChart, BarChartView, { parents: allowedParents('BarChart') }),
    Callout: guarded('Callout', CHAT_PROP_SCHEMAS.Callout, CalloutView, { parents: allowedParents('Callout') }),
    Button: guarded('Button', CHAT_PROP_SCHEMAS.Button, ButtonView, { parents: allowedParents('Button') }),
  },
});
