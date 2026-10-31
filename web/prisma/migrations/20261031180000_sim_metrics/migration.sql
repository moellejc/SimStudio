-- Scalar metrics produced by post.metrics_to_rows.
CREATE TABLE "sim_metrics" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "sim_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "value" DOUBLE PRECISION,
    "unit" TEXT NOT NULL,

    CONSTRAINT "sim_metrics_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "sim_metrics_sim_id_name_key" ON "sim_metrics"("sim_id", "name");
CREATE INDEX "sim_metrics_sim_id_idx" ON "sim_metrics"("sim_id");

ALTER TABLE "sim_metrics" ADD CONSTRAINT "sim_metrics_sim_id_fkey" FOREIGN KEY ("sim_id") REFERENCES "simulations"("id") ON DELETE CASCADE ON UPDATE CASCADE;
