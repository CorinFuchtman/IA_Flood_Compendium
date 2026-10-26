# News Impact Extraction Prompt (Large Language Model)

Following GroundSource practice, the exact prompt used to turn news articles
into structured impact records is published here so extraction is reproducible
and auditable. Any capable large language model can run it; records produced
this way carry `source_type = local_news` and the article URL in `source_ref`.

## Pipeline

1. Query a news search engine per episode with: Iowa + county or city names +
   "flooding" + the episode date window (from `NOAA_Final_2021_2025.csv`).
2. Keep articles from identifiable outlets whose publication date falls inside
   the window or up to 14 days after it.
3. Log every article consulted in `data/sources_news.csv` (including articles
   that yielded no records).
4. Run the prompt below per article; validate the JSON; geocode locations to a
   city or town centroid (GNIS or Census places); assign `episode_id` by date
   window + county overlap; merge consecutive-day duplicates.

## Prompt

```
You are extracting flood impact observations from a news article.

Step 1. Decide if the article documents flooding that already happened in
Iowa. Reject forecasts, warnings, preparedness stories, retrospectives about
other years, and articles about other states. If rejected, return
{"records": []}.

Step 2. For every place the article explicitly says was flooded or impacted,
create one record per impact type with these fields:
  location_name: "City, County, IA" at the finest level stated. Never invent
    a finer location than the text supports.
  location_raw: the verbatim location words (street names as written).
  impact_type: one of road_flooded, road_closed, bridge_damaged, rescue,
    evacuation, home_flooded, business_flooded, agriculture, infrastructure,
    injury, fatality, river_overbank, other.
  start_date, end_date: YYYY-MM-DD dates the impact was observed. Anchor
    relative phrases ("Tuesday", "yesterday") to the publication date. Dates
    must not exceed the publication date. If only a vague phrase is given
    ("recently"), reject the record.
  quantity, quantity_unit: a number stated in the text (homes, people,
    rescues, dollars), else blank.
  severity: 0 overbank only, 1 minor, 2 moderate (closures, flooded
    structures, rescues), 3 major (fatalities, evacuations, destroyed
    structures).
  text_span: the verbatim sentence supporting the record.

Step 3. Return JSON: {"records": [...]}. Extract only what the text states.
Do not infer, do not summarize, do not deduplicate across articles.
```

## Quality control

* Confidence A requires an explicit location and date; B allows county-level
  location or a 1-day date approximation; C marks single-source uncorroborated
  records.
* A random sample of records should be re-checked against the source text
  using the GroundSource rubric (Accurate / Approximate / Partial / Wrong)
  before each release; report the share of Accurate + Approximate.
* Cross-check news records against NOAA Storm Events by county and date
  window; agreement raises confidence, and news-only records are the layer's
  added value (impacts NOAA missed).
