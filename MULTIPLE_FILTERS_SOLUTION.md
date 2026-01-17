# Multiple Filters Implementation - Solution

## Problem
The issue requested that multiple filters should be formatted correctly according to the GraphQL schema. The expected format for combining filters like gender and tags should be:

```json
{
  "performer_filter": {
    "gender": {
      "value": "FEMALE",
      "modifier": "EQUALS"
    },
    "tags": {
      "value": [1],
      "modifier": "INCLUDES_ALL",
      "depth": 0
    }
  }
}
```

## Root Cause
The code was incorrectly using `value_list` for ID-based filters (tags, studios) when array-based modifiers were used. This was because the logic treated all array-based modifiers (`INCLUDES`, `EXCLUDES`, `INCLUDES_ALL`) the same way, regardless of whether the filter was enum-based or ID-based.

## Solution
Updated the filter conversion logic in `plugins/hotornot/hotornot.js`:

1. **Tags Filter (lines 292-306)**: 
   - Removed conditional logic that used `value_list` for array-based modifiers
   - Now always uses `value` field for tag IDs
   - Added clarifying comment

2. **Studios Filter (lines 309-323)**:
   - Removed conditional logic that used `value_list` for array-based modifiers  
   - Now always uses `value` field for studio IDs
   - Added clarifying comment

3. **Updated Documentation (lines 19-21)**:
   - Clarified that `value_list` is only for enum-based fields
   - Clarified that ID-based filters always use `value`

## Why This Works
The GraphQL schema expects:
- **ID-based filters** (tags, studios): Always use `value` field, even when the value is an array
- **Enum-based filters** (gender): Use `value` for single-value modifiers (EQUALS), `value_list` for array-based modifiers (INCLUDES, EXCLUDES)

This distinction is important because:
- Tag/studio IDs are integers, and the GraphQL schema expects them in a `value` array
- Gender values are enum strings, and the GraphQL schema expects them in either `value` (single) or `value_list` (multiple)

## Testing
Created a standalone test that validates:
- ✅ Tags with INCLUDES_ALL uses `value` field
- ✅ Gender with EQUALS uses `value` field  
- ✅ Multiple filters combine correctly
- ✅ Output matches expected format

## Impact
Users can now:
- Apply multiple filters simultaneously (e.g., filter by gender AND tags)
- Filters will be properly sent to the GraphQL API
- The HotOrNot plugin will correctly respect all selected filters
