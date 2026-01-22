# HotOrNot Plugin

A plugin for [Stash](https://stashapp.cc/) that uses an ELO-style rating system to rank performers and images through head-to-head comparisons.

## Features

**Comparison Modes:**
- **Swiss** ⚖️ – Fair matchups between similarly-rated items with recency weighting to reduce repetition (available for performers and images)
- **Gauntlet** 🎯 – Place a performer in your rankings by climbing from the bottom until they lose (performers only)
- **Champion** 🏆 – Winner stays on, with reduced rating changes for stable rankings (performers only)

**Smart Tracking:**
- Comprehensive statistics: wins, losses, streaks, win rates, and match history
- Adaptive K-factor: new performers adjust faster, established performers maintain stable rankings
- Dynamic filter support: respects your active Stash filters (tags, studios, favorites, etc.)

**Performance Optimized:**
- Performers: Full dataset for accurate ranking across all library sizes
- Images: Intelligent sampling (500) for libraries >1000 images

## Installation

⚠️ **Backup your database first** (Settings → Interface → Editing)

1. Download the `/plugins/hotornot/` folder to your Stash plugins directory
2. Optional: Change Rating System Type to "Decimal" for better precision (Settings → Interface → Editing)

## Usage

**For Performers:**
1. Navigate to the Performers page in Stash
2. Optional: Apply filters (tags, studios, favorites, etc.) - the plugin respects your active filters
3. Click the 🔥 button in the bottom-right corner
4. Choose your comparison mode (Swiss/Gauntlet/Champion)
5. Click a performer (or use arrow keys) to pick the winner
6. Click "📊 View All Stats" to see comprehensive statistics

**For Images:**
1. Navigate to the Images page in Stash
2. Click the 🔥 button in the bottom-right corner
3. Click an image (or use arrow keys) to pick the winner

## How It Works

**ELO Rating System:**
- Ratings stored in Stash's native `rating100` field (1-100 scale)
- Beating higher-rated items earns more points
- Losing to lower-rated items costs more points

**Adaptive K-Factor:**
- New performers (<10 matches): K=32 for fast initial positioning
- Moderately established (10-30 matches): K=24 for balanced adjustments
- Well-established (30+ matches): K=16 for stable rankings

**Scene Count Weighting (Performers):**
Performers with more scenes have more stable ratings (lower K-factor):
- 100+ scenes: 50% K-factor (very stable)
- 50-99 scenes: 65% K-factor
- 20-49 scenes: 80% K-factor
- 10-19 scenes: 90% K-factor
- <10 scenes: Full K-factor (no reduction)

**Statistics Tracking:**
Tracks wins, losses, draws (skips), streaks, win rates, and match history in the `hotornot_stats` custom field.

**Skip as ELO Draw (Swiss Mode):**
When you skip a comparison in Swiss mode, it's treated as a draw per standard ELO rules:
- Both performers receive a score of 0.5 (instead of 1 for win, 0 for loss)
- Higher-rated performer loses points for not defeating the underdog
- Lower-rated performer gains points for holding their own against the favorite
- Equally-rated performers experience no rating change
- Draws reset the current win/loss streak to zero

**Mode-Specific Behavior:**
- **Swiss**: Full stats and normal rating changes for both participants
- **Gauntlet**: Full stats for active challenger; defenders get participation tracking only
- **Champion**: Full stats for both, but 50% reduced K-factor for gradual evolution

**Recency Weighting (Swiss Mode):**
Recently matched performers are less likely to reappear:
- 0-1 hours ago: ~4% chance
- 1-6 hours ago: ~12% chance
- 6-24 hours ago: ~25% chance
- 24+ hours ago: ~50% chance

## Requirements

- Stash v0.27 or later
- At least 2 performers or images in your library

## Credits

Inspired by [stash-battle](https://github.com/dtt-git/stash-battle) by dtt-git

## License

See [LICENCE](LICENCE) for details.
