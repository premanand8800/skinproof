# Why SkinProof matters

## Who it is for

1. **Shoppers at the mirror.** Anyone who spends weeks on a serum and then has to decide: rebuy, stop, or keep waiting. Today that decision is made by eye, under a different lamp every day.
2. **Honest brands and retailers** who can back a claim ("visibly reduces pores in 8 weeks") and want a result a customer can verify, instead of competing with every other unverifiable "clinically proven" line.
3. **Dermatology and aesthetic clinics** whose before/after photos are only convincing if the capture conditions match. A noise estimate per patient tells the clinician which differences are real.

## The problem, with sources

Facts below were found during research on 2026-11-01. Each has a link. Anything weaker than a primary source is marked.

**The money is large and moving online.**
- Skincare is a roughly USD 160 to 200 billion market in 2025. Estimates differ by publisher because definitions differ: Precedence Research 161.45 bn ([link](https://www.precedenceresearch.com/skin-care-products-market)), Oberlo 194.3 bn ([link](https://oberlo.com/statistics/skin-care-market)). Treat as a range, not a number.
- Online was about 26% of global beauty sales in 2024, up from 14% in 2015, per a secondary summary of McKinsey data ([link](https://www.clickpost.ai/blog/beauty-industry-ecommerce-market)). *Uncertain: the McKinsey original was not opened; this is beauty overall, not skincare alone.* US beauty and personal care online share was 39% in 2024 per Euromonitor ([link](https://www-euromonitor-com.lib.ezproxy.hkust.edu.hk/article/pricing-is-not-sephoras-achilles-heel-in-beauty-e-commerce), snippet only).
- Online shoppers cannot touch or test, so they rely even more on claims and their own photos.

**Shoppers do not believe the claims.**
- In a June 2024 survey of 3,224 American women (The Benchmarking Company), 86% believed some brands exaggerate what their ranges do, 66% had become more sceptical of pack claims, and about one in three could not tell a clinical claim from a consumer-perception claim ([link](https://cosmeticobs.com/en/articles/patterns-54/transparency-a-key-criteria-for-american-women-8255)). 90% wanted claims to say how they were established.
- UK Savanta survey of 2,003 consumers: 80% of women and 74% of men thought the skincare industry is flooded with misinformation ([link](https://www.countryandtownhouse.com/style/health-and-beauty/simple-truth-report/)). *Secondary report, survey not opened.*

**Returns do not capture the waste, which is a nuance in our favour and against us.** Sealed skincare is returned only about 3% of the time online, while foundation and concealer reach about 23% on shade mismatch ([Eightx benchmark](https://eightx.co/blog/average-beauty-and-cosmetics-return-rate-benchmarks), a consultancy compilation, not primary research). So the cost of a skincare product that does not work is not a return. It is a rebuy: money spent every month on something nobody verified. We do not have a number for that, and do not claim one.

**Photos of the same face disagree with themselves.**
- A dermatology review of clinical photography states that even small variations in capture "cause a drastic change in the photos and their clinical and research value" ([Nair and Santhanam, J Cutan Aesthet Surg 2016, PubMed 27398013](https://pubmed.ncbi.nlm.nih.gov/27398013/), doi 10.4103/0974-2077.184047). Clinics therefore use standardised angles and lighting.
- Evidence on AI skin scores specifically is thin and mixed. A vendor page cites a Wake Forest study with 95% test-retest reliability, but gives no paper and does not say whether light or pose changed ([Perfect Corp](https://www.perfectcorp.com/business/blog/ai-skincare/why-skincare-progress-photos-lie), *vendor claim, not verified*). A non-peer-reviewed preprint found low, standard and studio light did not significantly change an iPad skin app's scores (p=0.79) ([preprint](https://www.preprints.org/manuscript/202409.0822)). We did not find a peer-reviewed study that varies light and head pose and reports score spread for a smartphone skin analyser. That gap is why we measured it ourselves (next section).

**Efficacy takes weeks, so a single photo cannot judge a product.**
- A randomised, double-blind split-face trial of 5% niacinamide ran 12 weeks with images at 4-week intervals and found reductions in fine lines, spots and redness (Bissett et al., Dermatol Surg 2005, [PubMed 16029679](https://pubmed.ncbi.nlm.nih.gov/16029679/), doi 10.1111/j.1524-4725.2005.31732).
- A systematic review of 7 RCTs of topical tretinoin for photoaging (trials of 3 to 24 months) reported improvement "as early as 1 month" ([Sitohang et al., Int J Womens Dermatol 2022, PubMed 35620028](https://pubmed.ncbi.nlm.nih.gov/35620028/), doi 10.1097/JW9.0000000000000003). Consumer sites quote "4 to 8 weeks" for niacinamide and "8 to 12 weeks" for retinoids; those are blog figures, not trial results, so we use only the trial durations above. *Uncertain: no single source gives a canonical time-to-effect.*

**Retailers and brands are bound by evidence rules.**
- EU Regulation 655/2013 (common criteria for cosmetic claims), Annex, criterion 4 Honesty: "Presentations of a product's performance shall not go beyond the available supporting evidence" ([text](https://www.legislation.gov.uk/eur/2013/655/annex/division/4/adopted?view=plain)).
- In the US the FTC requires "competent and reliable scientific evidence" for health-related claims; its 2022 Health Products Compliance Guidance says randomised controlled human trials are generally what experts require, and that anecdotes alone are not enough ([FTC PDF](https://www.ftc.gov/system/files/ftc_gov/pdf/Health-Guidance-508.pdf), summarised by [Jones Day](https://m.jonesday.com/en/insights/2023/01/ftc-overhauls-and-expands-25yearold-health-products-advertising-compliance-guide)). *Uncertain: it is business guidance for health products, and how far it reaches cosmetic appearance claims was not verified.* The ASA (UK) position was not researched here.
- The point for retailers: a claim is easy to make and hard to show. "Claims vs measured" is a feature a retailer or brand can use, and a regulator would accept more readily than a score nobody can reproduce.

## How SkinProof addresses each part

| Problem | What SkinProof does | What was measured |
|---|---|---|
| Scores wobble, apps still show "progress" | Measures the wobble on the user's own face first (4 to 6 photos, one day), then only counts a change larger than `max(range, 2 x SD, 3 points)` | Same face, 10 seconds apart, same room: acne moved 9.4 to 9.9 points depending on crop, pores 6.0 to 8.3 ([bench/noise_RESULTS.md](../bench/noise_RESULTS.md)) |
| One odd photo ruins a comparison | Keeps the observed range, not only the SD, as the threshold | Six same-evening photos: one pore reading 35.9 against 58.4 to 63.8, a 27.9-point range; without that photo the range is 5.4 |
| A single lucky reading looks like proof | Confirmation rule: beyond noise on one check-in is only "likely"; proven needs two in a row, same direction. Agent may say KEEP or STOP only on proven | The false alarm: a 3 degree head turn moved pores 4.6 points against a 4.03 threshold and the first version said STOP. Today the same inputs give WAIT 2 weeks (README, `demo/agent_trace_live_synthetic.json`) |
| "Fix the light" is a tempting shortcut | Tested YouCam Photo Lighting with a pre-registered rule (adopt only if median range drops 25% or more) | Median range fell 22%, so the feature stays off ([bench/lighting_RESULTS.md](../bench/lighting_RESULTS.md)) |
| Claims are unverifiable | Extracts measurable claims (concern, quote, timeframe) from text, URL or label photo and marks each SUPPORTED, CONTRADICTED, NEEDS_CONFIRMATION, NOT_YET_SHOWN, TOO_EARLY or NOT_MEASURABLE | Unit tests on the comparison logic; the live volunteer run ends WAIT, nothing proven |
| Weeks needed | Says WAIT N weeks and TOO_EARLY instead of judging early | Agent trace shows the decision and each step with unit cost |
| Bad photos | Face Attributes quality gate recommends a retake; faces auto-cropped because YouCam rejects small faces | Before cropping YouCam rejected most photos with `error_src_face_too_small`; two turned heads were rejected with `error_large_face_angle` |

What we show for a retailer is simple: the product's own claim, the shopper's own measured noise, and one of six honest statuses. No overall score, no hidden step.

## What we have NOT proven

- **No longitudinal user study.** Nobody has used SkinProof for 8 weeks on a real product. The live run is one volunteer, one evening.
- **One volunteer, six photos.** The noise numbers are an existence proof that the wobble is large, not a population estimate. Other devices, skin tones and cameras may differ.
- **Proven is not caused-by.** Beyond your noise twice in a row means the measurement changed, not that the product did it.
- **Eight concerns tested at once.** Chance crossings remain possible. Every verdict states how many were tested.
- **No measured effect on purchase decisions or money saved.** We make no claim of savings.
- The market, survey and regulation figures above come from third parties, several secondary.

## Next steps

1. **Pilot with 10 to 20 shoppers for 8 weeks** on a product each already uses. Measure: how often the agent says WAIT vs KEEP vs STOP, whether users change their rebuy decision, and whether noise estimates transfer across weeks. Publish the raw results either way.
2. **Consistent-capture guide.** Same spot, same window, same time of day, distance and a guide frame, 5 to 6 calibration shots with slight head turns. The false alarm taught us that rotation must be in the calibration.
3. **Retailer "verified results" badge.** A brand or retailer lists a claim and timeframe; opted-in customers' check-ins are run through the same rule; the badge shows the share of customers whose measurement moved beyond their own noise twice in a row, with the count tested. Aggregate only, never photos.
4. **Clinic workflow.** Calibrate each patient's noise at the first visit, then flag which before/after differences exceed it. Requires clinical review and, in many places, regulatory care; we are not a medical device.
5. **Validate against a reference.** Compare YouCam raw scores with a clinical imaging system on the same subjects to learn which concerns are measurable at all (pores already look unreliable).
