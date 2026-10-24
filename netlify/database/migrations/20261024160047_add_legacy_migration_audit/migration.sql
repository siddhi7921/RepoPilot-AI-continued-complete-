CREATE TABLE "legacy_accounts" (
	"key" text PRIMARY KEY,
	"owner_id" text NOT NULL,
	"metadata" jsonb NOT NULL
);
--> statement-breakpoint
CREATE TABLE "migration_log" (
	"key" text PRIMARY KEY,
	"source_id" text NOT NULL,
	"legacy_id" text NOT NULL,
	"owner_id" text NOT NULL,
	"report_id" text NOT NULL,
	"digest" text NOT NULL,
	"actor_id" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
