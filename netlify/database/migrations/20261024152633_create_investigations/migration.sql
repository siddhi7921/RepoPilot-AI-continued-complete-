CREATE TABLE "investigations" (
	"id" text PRIMARY KEY,
	"owner_id" text NOT NULL,
	"report" jsonb NOT NULL,
	"version" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX "investigations_owner_created_idx" ON "investigations" ("owner_id","created_at");