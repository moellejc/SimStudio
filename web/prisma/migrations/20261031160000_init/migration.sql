-- CreateSchema
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- CreateTable
CREATE TABLE "users" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "email" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "password_hash" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "simulations" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "user_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'queued',
    "progress" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "message" TEXT NOT NULL DEFAULT '',
    "error_message" TEXT,
    "source_type" TEXT NOT NULL,
    "template_id" TEXT,
    "material" TEXT,
    "dry_run" BOOLEAN NOT NULL DEFAULT true,
    "parameters" JSONB,
    "summary" JSONB,
    "peak_force_n" DOUBLE PRECISION,
    "max_von_mises_pa" DOUBLE PRECISION,
    "max_plastic_strain" DOUBLE PRECISION,
    "energy_balance_error_pct" DOUBLE PRECISION,
    "s3_prefix" TEXT,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "started_at" TIMESTAMPTZ(3),
    "finished_at" TIMESTAMPTZ(3),

    CONSTRAINT "simulations_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "artifacts" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "simulation_id" UUID NOT NULL,
    "kind" TEXT NOT NULL,
    "filename" TEXT NOT NULL,
    "s3_key" TEXT NOT NULL,
    "content_type" TEXT NOT NULL,
    "size_bytes" INTEGER NOT NULL,

    CONSTRAINT "artifacts_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");

-- CreateIndex
CREATE INDEX "simulations_user_id_created_at_idx" ON "simulations"("user_id", "created_at");

-- CreateIndex
CREATE INDEX "artifacts_simulation_id_idx" ON "artifacts"("simulation_id");

-- AddForeignKey
ALTER TABLE "simulations" ADD CONSTRAINT "simulations_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "artifacts" ADD CONSTRAINT "artifacts_simulation_id_fkey" FOREIGN KEY ("simulation_id") REFERENCES "simulations"("id") ON DELETE CASCADE ON UPDATE CASCADE;
