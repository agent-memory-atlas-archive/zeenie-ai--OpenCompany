/**
 * The setup screen for json-render: the hire catalogue (catalog.ts's
 * HIRE_CATALOG) and its registry, a guarded component for each of the 18
 * types (views.tsx drawn through lib/jsonRender/guard.tsx, which reads each
 * element's props through the catalogue's forgiving schema first, so an odd
 * value degrades one prop rather than the element).
 *
 * The catalogue is defined here rather than in catalog.ts because this
 * module loads only with the screen (HireScreen), while catalog.ts is in
 * Home's first chunk; defining it there would bring json-render along.
 */

import { defineCatalog } from '@json-render/core';
import { defineRegistry, schema } from '@json-render/react';
import { guarded } from '@/lib/jsonRender';
import { HIRE_CATALOG, PROP_SCHEMAS } from './catalog';
import {
  AgentCardView,
  BadgeView,
  ButtonView,
  CardView,
  ChoiceView,
  DividerView,
  DraftView,
  GridView,
  HeadingView,
  InputView,
  ListView,
  MetricView,
  PlanView,
  ProgressView,
  ScheduleView,
  StackView,
  TextView,
  ToggleView,
} from './views';

export const hireCatalog = defineCatalog(schema, HIRE_CATALOG);

export const { registry: hireRegistry } = defineRegistry(hireCatalog, {
  components: {
    Stack: guarded('Stack', PROP_SCHEMAS.Stack, StackView),
    Grid: guarded('Grid', PROP_SCHEMAS.Grid, GridView),
    Card: guarded('Card', PROP_SCHEMAS.Card, CardView),
    Heading: guarded('Heading', PROP_SCHEMAS.Heading, HeadingView),
    Text: guarded('Text', PROP_SCHEMAS.Text, TextView),
    Metric: guarded('Metric', PROP_SCHEMAS.Metric, MetricView),
    Badge: guarded('Badge', PROP_SCHEMAS.Badge, BadgeView),
    Plan: guarded('Plan', PROP_SCHEMAS.Plan, PlanView),
    Schedule: guarded('Schedule', PROP_SCHEMAS.Schedule, ScheduleView),
    AgentCard: guarded('AgentCard', PROP_SCHEMAS.AgentCard, AgentCardView),
    List: guarded('List', PROP_SCHEMAS.List, ListView),
    Draft: guarded('Draft', PROP_SCHEMAS.Draft, DraftView),
    Progress: guarded('Progress', PROP_SCHEMAS.Progress, ProgressView),
    Toggle: guarded('Toggle', PROP_SCHEMAS.Toggle, ToggleView),
    Choice: guarded('Choice', PROP_SCHEMAS.Choice, ChoiceView),
    Input: guarded('Input', PROP_SCHEMAS.Input, InputView),
    Button: guarded('Button', PROP_SCHEMAS.Button, ButtonView),
    Divider: guarded('Divider', PROP_SCHEMAS.Divider, DividerView),
  },
});
