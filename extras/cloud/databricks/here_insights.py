# Databricks notebook source
# MAGIC %md
# MAGIC # HERE insights
# MAGIC
# MAGIC Reads the flat event export from the memory service (`python -m extras.memory export events.jsonl`, uploaded
# MAGIC to a Unity Catalog volume), computes the day's metrics with Spark, writes them as Delta tables, and renders the
# MAGIC dashboard insights with the **same** `extras/cloud/insights_core.render()` the local fallback uses, tagged
# MAGIC `source: "databricks"`.
# MAGIC
# MAGIC Text and metadata only: no audio ever reaches this job (SPEC §25).
# MAGIC
# MAGIC Run it as a job (`job.json`) or interactively. `python -m extras.cloud.databricks_sync --create-job` uploads it
# MAGIC to `/Workspace/Shared/here/` next to `extras/cloud/insights_core.py` and `extras/memory/replay.py`, which it imports.

# COMMAND ----------

dbutils.widgets.text("export_path", "/Volumes/workspace/here/exports/events.jsonl")
dbutils.widgets.text("output_path", "/Volumes/workspace/here/exports/insights_databricks.json")
dbutils.widgets.text("catalog_schema", "workspace.here")
dbutils.widgets.text("tz_offset_h", "-5")
dbutils.widgets.text("user_name", "Kori")
export_path = dbutils.widgets.get("export_path")
output_path = dbutils.widgets.get("output_path")
schema = dbutils.widgets.get("catalog_schema")
tz_offset_h = float(dbutils.widgets.get("tz_offset_h"))
user_name = dbutils.widgets.get("user_name").lower()

# COMMAND ----------

import json
import os
import sys

# the uploaded tree's root (/Workspace/Shared/here): this notebook lives in extras/cloud/databricks/
repo_root = os.path.abspath(os.path.join(os.getcwd(), "..", "..", ".."))
sys.path.insert(0, repo_root)
from extras.cloud.insights_core import HAZARDS, POLICIES, SPEECHY, STOP, render  # noqa: E402

from pyspark.sql import functions as F  # noqa: E402

events = spark.read.json(export_path)
events = events.withColumn("hour", (F.floor(((F.col("event_time") + F.lit(tz_offset_h * 3600)) % 86400) / 3600))
                           .cast("int"))
events.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{schema}.events")
display(events.groupBy("type").count())

# COMMAND ----------

main = events.where(F.col("type").isin("utterance", "sound_event", "alert"))
sounds = events.where(F.col("type") == "sound_event")
utts = events.where(F.col("type") == "utterance")
enrich = events.where(F.col("type") == "perception_enrichment").select(F.col("ref_id"), F.col("label").alias("attention"))

per_hour = {str(r["hour"]): r["count"] for r in main.groupBy("hour").count().orderBy("hour").collect()}

is_speech = (F.col("type") == "utterance") | ((F.col("type") == "sound_event") & F.col("label").isin(*SPEECHY))
loc = (events.where(F.col("type").isin("utterance", "sound_event"))
       .withColumn("kind", F.when(is_speech, "speech").otherwise("environment"))
       .groupBy(F.coalesce("location_id", F.lit("unknown")).alias("loc")).pivot("kind", ["speech", "environment"])
       .count().fillna(0))
by_loc = {r["loc"]: {"speech": r["speech"], "environment": r["environment"]} for r in loc.collect()}

n_sounds = sounds.count()
unknown_ids = [r["ref_id"] for r in sounds.where(F.col("label") == "unknown").select("ref_id").collect()]

rear = (events.where((F.col("type") == "alert") & F.col("rel_bearing").isNotNull() & (F.abs("rel_bearing") >= 120)
                     & (F.col("payload.priority") >= 2))
        .select(F.col("ref_id").alias("event_id"), F.col("event_time").alias("t"), "label", "rel_bearing",
                "segment_id").orderBy("t"))
rear_rows = [r.asDict() for r in rear.collect()]

partners = (utts.where(F.col("person_label").isNotNull()).groupBy("person_label")
            .agg(F.count("*").alias("n"), F.collect_set("segment_id").alias("segs")).orderBy(F.desc("n")).collect())

stop = sorted(STOP | {user_name})
words = (utts.select("ref_id", "segment_id", F.explode(F.array_distinct(
            F.split(F.regexp_replace(F.lower("text"), "[^a-z' ]", " "), " +"))).alias("w"))
         .where((F.length("w") >= 4) & ~F.col("w").isin(*stop)))
topics = (words.groupBy("w").agg(F.count("*").alias("count"), F.collect_set("segment_id").alias("segments"),
                                 F.collect_list("ref_id").alias("ids"))
          .where(F.size("segments") >= 2).orderBy(F.desc(F.size("segments")), F.desc("count"), "w").collect())

span = main.where(F.col("segment_id").isNotNull()).groupBy("segment_id").agg(
    ((F.max("event_time") - F.min("event_time")) / 60).alias("minutes"))
density = (sounds.groupBy("segment_id").count().join(span, "segment_id")
           .select("segment_id", F.round(F.col("count") / F.greatest(F.lit(1.0), "minutes"), 2).alias("per_min"))
           .collect())

ss = (events.where(F.col("type").isin("utterance", "sound_event")).join(enrich, "ref_id")
      .groupBy(F.coalesce("segment_id", F.lit("unknown")).alias("seg"))
      .agg(F.sum(F.when(F.col("attention") != "ambient", 1).otherwise(0)).alias("surfaced"),
           F.sum(F.when(F.col("attention") == "ambient", 1).otherwise(0)).alias("suppressed")).collect())

# COMMAND ----------

# Replay scenarios: the Replay Lab policy engine over each segment (small: one day is a few hundred rows).
from extras.memory.replay import replay  # noqa: E402

# Read the export itself (a few hundred lines): on serverless (Spark Connect) DataFrame.toJSON() yields Rows, and
# the file keeps nulls and payloads exactly as the local fallback sees them.
with open(export_path, encoding="utf-8") as f:
    rows = [json.loads(line) for line in f if line.strip()]
scenarios = {}
for sid in sorted({r.get("segment_id") for r in rows if r.get("segment_id")}):
    seg_rows = [r for r in rows if r.get("segment_id") == sid]
    scenarios[sid] = {name: {k: v for k, v in replay(seg_rows, pol, use_model=False).items()
                             if k in ("surfaced", "suppressed", "kept_important", "missed_important")}
                      for name, pol in POLICIES.items()}

# COMMAND ----------

metrics = {
    "events_per_hour": per_hour,
    "speech_vs_env_by_location": by_loc,
    "unknown_rate": {"unknown": len(unknown_ids), "sounds": n_sounds,
                     "rate": round(len(unknown_ids) / n_sounds, 3) if n_sounds else 0.0, "ids": unknown_ids},
    "rear_important": rear_rows,
    "partners": {r["person_label"]: r["n"] for r in partners},
    "partner_segments": {r["person_label"]: sorted(s for s in r["segs"] if s) for r in partners},
    "topics": {r["w"]: {"count": r["count"], "segments": sorted(r["segments"]), "ids": r["ids"]} for r in topics},
    "noise_density_per_min": {r["segment_id"]: r["per_min"] for r in density},
    "surfaced_vs_suppressed": {r["seg"]: {"surfaced": r["surfaced"], "suppressed": r["suppressed"]} for r in ss},
    "replay_scenarios": scenarios,
}
labels = {}
try:
    seg_meta = json.load(open(os.path.join(repo_root, "extras/memory/seed/segments.json"), encoding="utf-8"))
    labels = {s["id"]: s["label"] for s in seg_meta["segments"]}
    labels.update({loc_["id"]: loc_["name"] for loc_ in seg_meta["locations"]})
except OSError:
    pass
insights = render(metrics, "databricks", labels)

spark.createDataFrame([{**i, "evidence_ids": json.dumps(i["evidence_ids"]), "data": json.dumps(i["data"])}
                       for i in insights]).write.mode("overwrite").saveAsTable(f"{schema}.insights")
with open(output_path, "w", encoding="utf-8") as f:     # Unity Catalog volumes are plain paths on UC compute
    json.dump({"source": "databricks", "insights": insights}, f, ensure_ascii=False)
display(spark.table(f"{schema}.insights").select("score", "id", "title", "text").orderBy(F.desc("score")))

# COMMAND ----------

# MAGIC %md
# MAGIC Back on the memory server: `python -m extras.cloud.databricks_sync` pulls `insights_databricks.json` and stores the
# MAGIC rows with `source: "databricks"` (or, without network, `python -m extras.cloud.insights_local --push <file>`).
# MAGIC The dashboard keeps showing the precomputed insights until then.
