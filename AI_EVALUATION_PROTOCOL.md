# V1 evaluation protocol

Freeze the private 24-image set before tuning thresholds. Run every image with its
immutable request snapshot and a recorded seed; retain the complete eight-member
bundle and diagnostics. No evaluation image may be used for training.

Score the following gates:

1. Automatic target selection is correct on at least 90% of images. Every miss is
   recoverable through the returned 2–20 normalized candidate boxes.
2. Across expert-reviewed masks, median IoU is at least 0.90 and median boundary F1
   is at least 0.85. Empty, mostly-outside, near-full-frame, low-IoU, and
   edge-dominated masks fail explicitly.
3. Product pixels inside the eroded accepted mask are identical after composition
   and after text rendering. JPEG encoding is tested separately for valid MIME and
   deterministic settings; no grading or generative edit is permitted.
4. Instagram is exactly 1080×1080, Facebook 1200×630, and LinkedIn 1200×627.
   Every bundle has exact names, MIME, byte counts, dimensions, SHA-256 records,
   matching copy/manifest language, and a 1024×1024 generated background.
5. Every French and English campaign passes deterministic evidence equality.
   Adversarial invented benefits, ingredients, medical/statistical claims,
   certifications, and environmental claims must all fail. The release gate is
   zero unsupported claims.
6. Warm execution is at most five minutes and cold execution at most fifteen
   minutes on a fresh Colab T4. Record actual timings; never infer them.

Run CPU contract tests with:

```powershell
cd ai_core
python -m pytest
```

Record evaluation summaries without image contents or private filenames. A failed
gate blocks release and requires an evidence-backed threshold, UX, or model-policy
decision; it does not authorize training automatically.
