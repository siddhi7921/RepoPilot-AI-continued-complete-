import { pgTable, text, timestamp, jsonb, integer, index } from 'drizzle-orm/pg-core';
import type { Job } from '../server/investigation.js';

export const investigations = pgTable('investigations', {
  id: text('id').primaryKey(),
  ownerId: text('owner_id').notNull(),
  report: jsonb('report').$type<Job>().notNull(),
  version: integer('version').notNull().default(0),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
}, table => [index('investigations_owner_created_idx').on(table.ownerId, table.createdAt)]);

export const legacyAccounts = pgTable('legacy_accounts', {
  key: text('key').primaryKey(),
  ownerId: text('owner_id').notNull(),
  metadata: jsonb('metadata').$type<{ username: string; created_at: string }>().notNull(),
});
export const migrationLog = pgTable('migration_log', {
  key: text('key').primaryKey(),
  sourceId: text('source_id').notNull(),
  legacyId: text('legacy_id').notNull(),
  ownerId: text('owner_id').notNull(),
  reportId: text('report_id').notNull(),
  digest: text('digest').notNull(),
  actorId: text('actor_id').notNull(),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
});
