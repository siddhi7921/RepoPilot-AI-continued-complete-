import { pgTable, text, timestamp, jsonb, integer, index } from 'drizzle-orm/pg-core';
import type { Job } from '../server/investigation.js';

export const investigations = pgTable('investigations', {
  id: text('id').primaryKey(),
  ownerId: text('owner_id').notNull(),
  report: jsonb('report').$type<Job>().notNull(),
  version: integer('version').notNull().default(0),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
}, table => [index('investigations_owner_created_idx').on(table.ownerId, table.createdAt)]);
