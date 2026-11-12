# Functionality audit — 11 November 2026

Audited the Streamlit scene lifecycle, globe controls, Analysis interactions,
saved-data recovery, and AI evidence/request handling. Existing visual changes
were preserved. No live CelesTrak or paid LLM requests were made.

## Fixes

- Clearing every group unmounted the globe but retained its delivery signature.
  Restoring the same groups could omit keyframes needed by the fresh iframe.
  Empty/error states and scene reload now clear that signature, and the signature
  includes the sampled time window.
- Globe ray picking could select satellites occluded by Earth. Picks now use the
  same Earth-occlusion check as selected labels.
- Releasing a globe drag could trigger satellite selection. Pointer movement now
  distinguishes dragging from clicking. Hidden points also explicitly discard
  fragments rather than relying on a zero point size being honored by WebGL.
- Satellite name matching could retrieve STARLINK-10 for STARLINK-100. Matching
  now requires complete name boundaries.
- The AI response-size limit previously checked an already-downloaded body.
  Bounded streaming now stops oversized responses during download and closes them.
- Malformed manifest structures and invalid text encodings could crash saved-data
  loading. They now degrade to recovery. A corrupt indexed latest snapshot can
  fall back to an older usable saved snapshot.

## Verification

- 226 pytest tests passed, including new lifecycle, exact-name, bounded-response,
  malformed-manifest and corrupt-snapshot regressions.
- Offline full browser smoke passed with 11,977 satellites: filters, selection,
  121-point trails, playback/reset, no additional API resources during playback,
  Analysis charts, diagnostics, group highlighting and linked selection.
- Targeted browser checks passed for front/back/limb picking, hidden groups,
  drag-release versus click, and no severe browser errors.
- AI browser flow passed three requests through a real local HTTP mock; no severe
  browser errors. This verifies transport/rendering, not live model answer quality.

Two test assumptions needed correction: a physics bound must compare mean orbital
elements and propagated heights at the same epoch, and the reset smoke test must
wait for the initial reveal then explicitly pause autoplay at time zero.
Pixel comparisons exclude the changing HTML FPS overlay and wait for the reveal.

## Limits

Browser verification used Chrome with software WebGL and offline snapshots.
Live upstream outages, real provider credentials/quota, hardware-specific GPU
behavior, and other browser engines were not exercised. This is a targeted
functional audit, not a guarantee that every possible failure has been eliminated.
