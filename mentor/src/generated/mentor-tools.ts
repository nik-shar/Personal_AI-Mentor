/**
 * AUTO-GENERATED — do not edit by hand.
 *
 * Source: api/tools.py (pydantic tool manifest)
 * Regenerate: uv run python scripts/tools/export_mentor_tools_schema.py
 */

export type MentorToolKind = "read" | "write";

export interface MentorToolDescriptor {
  /** Tool name as registered with pi.registerTool(). */
  name: string;
  /** The mentor question this tool answers. */
  intent: string;
  description: string;
  kind: MentorToolKind;
  /** JSON Schema of the tool parameters (generated from pydantic). */
  params: Record<string, unknown>;
  /** JSON Schema of the tool result (generated from pydantic). */
  result: Record<string, unknown>;
}

export const MENTOR_TOOLS_SERVICE = "mentor-sidecar";
export const MENTOR_TOOLS_VERSION = "0.1.0";
export const MENTOR_TOOLS_GENERATED_AT = "2026-09-17T11:44:43.753571+00:00";

export const MENTOR_TOOLS: MentorToolDescriptor[] = [
  {
    "name": "get_profile",
    "intent": "What do I know about him?",
    "description": "Read structured profile facts (identity, career targets, goals, learning state).",
    "kind": "read",
    "params": {
      "properties": {
        "keys": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Explicit profile fact keys to read. Omit for the default mentor set.",
          "title": "Keys"
        },
        "max_facts": {
          "default": 40,
          "maximum": 500,
          "minimum": 1,
          "title": "Max Facts",
          "type": "integer"
        }
      },
      "title": "GetProfileRequest",
      "type": "object"
    },
    "result": {
      "$defs": {
        "ProfileFact": {
          "properties": {
            "key": {
              "title": "Key",
              "type": "string"
            },
            "value": {
              "title": "Value"
            },
            "updated_at": {
              "anyOf": [
                {
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "title": "Updated At"
            }
          },
          "required": [
            "key",
            "value"
          ],
          "title": "ProfileFact",
          "type": "object"
        }
      },
      "properties": {
        "facts": {
          "items": {
            "$ref": "#/$defs/ProfileFact"
          },
          "title": "Facts",
          "type": "array"
        },
        "count": {
          "title": "Count",
          "type": "integer"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "facts",
        "count"
      ],
      "title": "GetProfileResponse",
      "type": "object"
    }
  },
  {
    "name": "get_identity",
    "intent": "Who is he, and what have I learned about him?",
    "description": "The composed picture of who Nik is: the structured profile PLUS what has been learned through conversation (facts, goals, preferences), each with its provenance and confidence, plus anything time-sensitive and anything still awaiting his confirmation. Richer than get_profile.",
    "kind": "read",
    "params": {
      "properties": {
        "limit": {
          "default": 40,
          "description": "Maximum learned memories (facts/goals/preferences) to include.",
          "maximum": 200,
          "minimum": 1,
          "title": "Limit",
          "type": "integer"
        },
        "include_profile": {
          "default": true,
          "description": "Include the structured profile half. Disable to read only what was learned through conversation.",
          "title": "Include Profile",
          "type": "boolean"
        }
      },
      "title": "GetIdentityRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "block": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "The identity block, ready to read: profile facts, learned memories with inline provenance, time-sensitive items, and unconfirmed inferences. None when nothing is known yet.",
          "title": "Block"
        },
        "found": {
          "description": "False when the mentor knows nothing about him yet — ask, don't guess.",
          "title": "Found",
          "type": "boolean"
        },
        "counts": {
          "additionalProperties": {
            "type": "integer"
          },
          "description": "Section sizes (profile_facts / who / due / pending_validation).",
          "title": "Counts",
          "type": "object"
        },
        "degraded": {
          "description": "Sources that could not be read. A partial answer, and it says which part.",
          "items": {
            "type": "string"
          },
          "title": "Degraded",
          "type": "array"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "found"
      ],
      "title": "GetIdentityResponse",
      "type": "object"
    }
  },
  {
    "name": "get_history",
    "intent": "What happened — what did we talk about, what did he do?",
    "description": "The narrative of the past, from four deterministic sources: the rolling summary of the thread so far, recent daily recaps, past conversations (when, how long, what about), and what he said he was doing in his own words. Read chronologically, not by similarity — so it does not change with how the question is phrased.",
    "kind": "read",
    "params": {
      "properties": {
        "sessions": {
          "default": 4,
          "description": "How many past conversations to include.",
          "maximum": 20,
          "minimum": 1,
          "title": "Sessions",
          "type": "integer"
        },
        "recaps": {
          "default": 3,
          "description": "How many daily recaps to include.",
          "maximum": 14,
          "minimum": 1,
          "title": "Recaps",
          "type": "integer"
        },
        "day_log": {
          "default": 25,
          "description": "How many day-log entries to read (rendered oldest-first).",
          "maximum": 200,
          "minimum": 1,
          "title": "Day Log",
          "type": "integer"
        }
      },
      "title": "GetHistoryRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "block": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "The narrative, ready to read: the rolling thread, recent daily recaps, past conversations, and what he said he was doing. None when there is no past yet.",
          "title": "Block"
        },
        "found": {
          "description": "False when nothing has happened yet — say so rather than inventing a past.",
          "title": "Found",
          "type": "boolean"
        },
        "counts": {
          "additionalProperties": {
            "type": "integer"
          },
          "description": "Source sizes (sessions / recaps / day_log / has_rolling_summary).",
          "title": "Counts",
          "type": "object"
        },
        "degraded": {
          "description": "Sources that could not be read. A partial story, and it names the missing part — an unreadable source is NOT the same as an empty one.",
          "items": {
            "type": "string"
          },
          "title": "Degraded",
          "type": "array"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "found"
      ],
      "title": "GetHistoryResponse",
      "type": "object"
    }
  },
  {
    "name": "recall_memories",
    "intent": "What did he say or do around this?",
    "description": "Semantic recall over older episodic memories (warm/cold tiers).",
    "kind": "read",
    "params": {
      "properties": {
        "query": {
          "description": "Natural-language query to search memory with",
          "minLength": 1,
          "title": "Query",
          "type": "string"
        },
        "limit": {
          "default": 5,
          "maximum": 20,
          "minimum": 1,
          "title": "Limit",
          "type": "integer"
        },
        "hot_threshold_days": {
          "default": 14,
          "description": "Only search memories older than this many days (the hot tier is already in context)",
          "maximum": 365,
          "minimum": 0,
          "title": "Hot Threshold Days",
          "type": "integer"
        }
      },
      "required": [
        "query"
      ],
      "title": "RecallMemoriesRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "block": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Formatted memory block, or None when nothing relevant",
          "title": "Block"
        },
        "found": {
          "title": "Found",
          "type": "boolean"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "found"
      ],
      "title": "RecallMemoriesResponse",
      "type": "object"
    }
  },
  {
    "name": "get_day_grid",
    "intent": "Am I free at four?",
    "description": "The 48-slot day grid with slot states, code-computed free windows, and the clock: which slots have already elapsed, and exactly how much of the day is left. Never offer a window the grid marks as elapsed.",
    "kind": "read",
    "params": {
      "properties": {
        "date": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "ISO date YYYY-MM-DD. Defaults to today in the user's timezone.",
          "title": "Date"
        }
      },
      "title": "GetDayGridRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "date": {
          "title": "Date",
          "type": "string"
        },
        "grid": {
          "additionalProperties": true,
          "description": "Slots (each carrying state, clock, and an `elapsed` / `current` mark) plus code-computed free windows and any read errors.",
          "title": "Grid",
          "type": "object"
        },
        "is_today": {
          "default": true,
          "description": "False for any date other than today. Then nothing is marked elapsed and elapsed_minutes is 0 — asking about tomorrow must not report hours already gone.",
          "title": "Is Today",
          "type": "boolean"
        },
        "now_clock": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "The clock reading the elapsed marks were computed against.",
          "title": "Now Clock"
        },
        "elapsed_minutes": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Minutes of the day already gone, computed in code.",
          "title": "Elapsed Minutes"
        },
        "remaining_minutes": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Minutes left in the day, computed in code. Do not estimate this — and do not offer a window that has already elapsed.",
          "title": "Remaining Minutes"
        },
        "errors": {
          "description": "Read failures collected while building the grid. An empty grid WITH errors here is unreadable, not unplanned — say so rather than claiming the day is free.",
          "items": {
            "type": "string"
          },
          "title": "Errors",
          "type": "array"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "date"
      ],
      "title": "GetDayGridResponse",
      "type": "object"
    }
  },
  {
    "name": "available_topic_nodes",
    "intent": "What can he study right now?",
    "description": "DAG traversal: in-progress nodes first, then unlocked not-started nodes.",
    "kind": "read",
    "params": {
      "properties": {
        "limit": {
          "default": 15,
          "maximum": 100,
          "minimum": 1,
          "title": "Limit",
          "type": "integer"
        }
      },
      "title": "AvailableTopicNodesRequest",
      "type": "object"
    },
    "result": {
      "$defs": {
        "TopicNode": {
          "properties": {
            "graph_title": {
              "title": "Graph Title",
              "type": "string"
            },
            "node_id": {
              "title": "Node Id",
              "type": "string"
            },
            "title": {
              "title": "Title",
              "type": "string"
            },
            "status": {
              "title": "Status",
              "type": "string"
            }
          },
          "required": [
            "graph_title",
            "node_id",
            "title",
            "status"
          ],
          "title": "TopicNode",
          "type": "object"
        }
      },
      "properties": {
        "nodes": {
          "items": {
            "$ref": "#/$defs/TopicNode"
          },
          "title": "Nodes",
          "type": "array"
        },
        "count": {
          "title": "Count",
          "type": "integer"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "nodes",
        "count"
      ],
      "title": "AvailableTopicNodesResponse",
      "type": "object"
    }
  },
  {
    "name": "get_momentum",
    "intent": "Is he actually consistent?",
    "description": "Streak, completion rates, and momentum trend computed fresh from schedule events.",
    "kind": "read",
    "params": {
      "description": "No parameters — momentum is always computed for the current moment.",
      "properties": {},
      "title": "GetMomentumRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "streak_days": {
          "title": "Streak Days",
          "type": "integer"
        },
        "completion_rate_today": {
          "title": "Completion Rate Today",
          "type": "number"
        },
        "completion_rate_7d": {
          "title": "Completion Rate 7D",
          "type": "number"
        },
        "momentum_trend": {
          "title": "Momentum Trend",
          "type": "string"
        },
        "blocks_today": {
          "title": "Blocks Today",
          "type": "integer"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "streak_days",
        "completion_rate_today",
        "completion_rate_7d",
        "momentum_trend",
        "blocks_today"
      ],
      "title": "GetMomentumResponse",
      "type": "object"
    }
  },
  {
    "name": "save_daily_plan",
    "intent": "Lock in today's plan",
    "description": "Persist a daily plan (profile facts + episodic event) and echo the budget the code used.",
    "kind": "write",
    "params": {
      "$defs": {
        "PlanItemModel": {
          "properties": {
            "title": {
              "minLength": 1,
              "title": "Title",
              "type": "string"
            },
            "category": {
              "default": "learning",
              "description": "learning | project | admin | break ...",
              "title": "Category",
              "type": "string"
            },
            "priority": {
              "default": "should",
              "description": "must | should | nice-to-have",
              "title": "Priority",
              "type": "string"
            },
            "duration_min": {
              "default": 30,
              "maximum": 600,
              "minimum": 5,
              "title": "Duration Min",
              "type": "integer"
            },
            "linked_goal": {
              "anyOf": [
                {
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "title": "Linked Goal"
            },
            "notes": {
              "anyOf": [
                {
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "title": "Notes"
            },
            "scheduled_time": {
              "anyOf": [
                {
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "title": "Scheduled Time"
            }
          },
          "required": [
            "title"
          ],
          "title": "PlanItemModel",
          "type": "object"
        }
      },
      "properties": {
        "items": {
          "items": {
            "$ref": "#/$defs/PlanItemModel"
          },
          "minItems": 1,
          "title": "Items",
          "type": "array"
        },
        "available_minutes": {
          "maximum": 1440,
          "minimum": 0,
          "title": "Available Minutes",
          "type": "integer"
        },
        "date": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "ISO date; defaults to today in the user's timezone",
          "title": "Date"
        }
      },
      "required": [
        "items",
        "available_minutes"
      ],
      "title": "SaveDailyPlanRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "saved": {
          "title": "Saved",
          "type": "boolean"
        },
        "message": {
          "title": "Message",
          "type": "string"
        },
        "total_minutes": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Total Minutes"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "saved",
        "message"
      ],
      "title": "SaveDailyPlanResponse",
      "type": "object"
    }
  },
  {
    "name": "find_available_slots",
    "intent": "Where could this actually fit?",
    "description": "Candidate placement windows for a duration, computed from the grid; a high energy level nudges earlier windows first.",
    "kind": "read",
    "params": {
      "properties": {
        "date": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "ISO date; defaults to today in the user's timezone",
          "title": "Date"
        },
        "duration_min": {
          "default": 60,
          "maximum": 600,
          "minimum": 30,
          "title": "Duration Min",
          "type": "integer"
        },
        "energy_level": {
          "anyOf": [
            {
              "maximum": 5,
              "minimum": 1,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Energy Level"
        }
      },
      "title": "FindAvailableSlotsRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "date": {
          "title": "Date",
          "type": "string"
        },
        "duration_min": {
          "title": "Duration Min",
          "type": "integer"
        },
        "candidates": {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "title": "Candidates",
          "type": "array"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "date",
        "duration_min"
      ],
      "title": "FindAvailableSlotsResponse",
      "type": "object"
    }
  },
  {
    "name": "place_time_block",
    "intent": "Put this on my calendar",
    "description": "Validate and persist one booked block. Code-enforced: 30-minute alignment, overlap check, and an anchor guard that refuses to place tasks over sleep/meal/commute/gym.",
    "kind": "write",
    "params": {
      "properties": {
        "title": {
          "minLength": 1,
          "title": "Title",
          "type": "string"
        },
        "start_time": {
          "description": "ISO 8601 datetime, offset allowed, e.g. 2099-01-01T09:00:00+00:00",
          "title": "Start Time",
          "type": "string"
        },
        "duration_min": {
          "maximum": 600,
          "minimum": 30,
          "title": "Duration Min",
          "type": "integer"
        },
        "category": {
          "default": "learning",
          "title": "Category",
          "type": "string"
        },
        "priority": {
          "default": "should",
          "description": "must | should | nice-to-have",
          "title": "Priority",
          "type": "string"
        },
        "block_kind": {
          "default": "task",
          "title": "Block Kind",
          "type": "string"
        },
        "notes": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Notes"
        }
      },
      "required": [
        "title",
        "start_time",
        "duration_min"
      ],
      "title": "PlaceTimeBlockRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "status": {
          "title": "Status",
          "type": "string"
        },
        "event": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Event"
        },
        "slots": {
          "items": {
            "type": "string"
          },
          "title": "Slots",
          "type": "array"
        },
        "start_clock": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Start Clock"
        },
        "end_clock": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "End Clock"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "status"
      ],
      "title": "PlaceTimeBlockResponse",
      "type": "object"
    }
  },
  {
    "name": "set_anchor",
    "intent": "Protect my sleep/meals/commute/gym",
    "description": "Reserve contiguous slots as a recurring life anchor that task placement can never overwrite.",
    "kind": "write",
    "params": {
      "properties": {
        "start_time": {
          "description": "Wall-clock ISO 8601 datetime for the anchor's start, in the user's timezone (e.g. '2026-09-15T22:00:00'). Offsets are tolerated but the clock reading is what counts — 22:00 always means 22:00 for him.",
          "title": "Start Time",
          "type": "string"
        },
        "duration_min": {
          "description": "Length in minutes (rounded to 30). An anchor may span midnight — sleep 22:00 -> 09:00 is 660 — so the cap is a full day, not ten hours.",
          "maximum": 1440,
          "minimum": 30,
          "title": "Duration Min",
          "type": "integer"
        },
        "state": {
          "default": "meal",
          "description": "sleep | meal | commute | gym",
          "title": "State",
          "type": "string"
        },
        "label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Label"
        }
      },
      "required": [
        "start_time",
        "duration_min"
      ],
      "title": "SetAnchorRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "status": {
          "title": "Status",
          "type": "string"
        },
        "slots": {
          "description": "Clock labels claimed, in order (may span two dates).",
          "items": {
            "type": "string"
          },
          "title": "Slots",
          "type": "array"
        },
        "slot_count": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "How many 30-minute slots were actually written.",
          "title": "Slot Count"
        },
        "dates": {
          "description": "Every date written. Two entries means the anchor crosses midnight.",
          "items": {
            "type": "string"
          },
          "title": "Dates",
          "type": "array"
        },
        "spans_midnight": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Spans Midnight"
        },
        "summary": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Ready-to-quote sentence naming both windows, e.g. 'sleep anchored 2026-09-15 22:00-24:00 and 2026-09-16 00:00-09:00'.",
          "title": "Summary"
        },
        "anchor": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Anchor"
        },
        "label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Label"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "status"
      ],
      "title": "SetAnchorResponse",
      "type": "object"
    }
  },
  {
    "name": "log_learning_session",
    "intent": "Record what I studied today",
    "description": "Log a learning session and advance/reset the streak using the same arithmetic the mentor reasons with.",
    "kind": "write",
    "params": {
      "properties": {
        "topics": {
          "items": {
            "type": "string"
          },
          "title": "Topics",
          "type": "array"
        },
        "is_no_learning_day": {
          "default": false,
          "title": "Is No Learning Day",
          "type": "boolean"
        },
        "source": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Source"
        },
        "date": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "ISO date; defaults to today in the user's timezone",
          "title": "Date"
        }
      },
      "title": "LogLearningSessionRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "logged": {
          "title": "Logged",
          "type": "boolean"
        },
        "new_streak": {
          "title": "New Streak",
          "type": "integer"
        },
        "note": {
          "default": "",
          "title": "Note",
          "type": "string"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "logged",
        "new_streak"
      ],
      "title": "LogLearningSessionResponse",
      "type": "object"
    }
  },
  {
    "name": "log_day_event",
    "intent": "Log what I'm doing right now",
    "description": "Record one moment of his actual day (waking, starting, switching, lunch, sleep) in his own words, and get back the interval it closed with its duration. Raw evidence — no interpretation is stored, and durations are computed here.",
    "kind": "write",
    "params": {
      "properties": {
        "kind": {
          "description": "What kind of moment this is. 'wake' (up for the day), 'start' (beginning an activity), 'switch' (moving to a different one), 'break' (stepping away), 'done' (finished for now), 'sleep' (going to bed — pair it with 'wake' so the night itself gets a duration), or 'note' (an aside that changes nothing).",
          "title": "Kind",
          "type": "string"
        },
        "activity": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "What he is doing, in HIS words — 'learning', 'applying to jobs', 'outreach'. Never normalised into a tidier category: his phrasing is itself the signal, and a tidy label throws it away.",
          "title": "Activity"
        },
        "at": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Wall-clock ISO 8601 for when it happened, in his timezone. Omit for 'right now' — then the arrival time of his message is the timestamp, which is nearly always what he means.",
          "title": "At"
        },
        "note": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Anything else he said, verbatim — 'exhausted', 'nothing to do, will do something random'. Vagueness is data here; pass it through.",
          "title": "Note"
        },
        "energy": {
          "anyOf": [
            {
              "maximum": 5,
              "minimum": 1,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Only when he actually states it.",
          "title": "Energy"
        }
      },
      "required": [
        "kind"
      ],
      "title": "LogDayEventRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "logged": {
          "title": "Logged",
          "type": "boolean"
        },
        "kind": {
          "title": "Kind",
          "type": "string"
        },
        "activity": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Activity"
        },
        "at": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Resolved wall-clock ISO timestamp.",
          "title": "At"
        },
        "clock": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "'HH:MM' in his timezone.",
          "title": "Clock"
        },
        "closed": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "The interval this moment closed, when one was open: {activity, start, end, duration_min}. Duration is computed here, in code.",
          "title": "Closed"
        },
        "open_now": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "True when this moment left a new interval open.",
          "title": "Open Now"
        },
        "duplicate": {
          "default": false,
          "description": "True when an identical entry had already been logged moments before.",
          "title": "Duplicate",
          "type": "boolean"
        },
        "message": {
          "default": "",
          "title": "Message",
          "type": "string"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "logged",
        "kind"
      ],
      "title": "LogDayEventResponse",
      "type": "object"
    }
  },
  {
    "name": "run_specialist",
    "intent": "Build me a roadmap / tailor my resume / draft a post",
    "description": "Run one specialist agent — goal_decomposer (learning roadmaps), job_hunter (resume tailoring, application pipeline), or linkedin_writer (post drafts) — and persist its memory delta. Python keeps these specialists and owns every store they touch; this is only the routing seam.",
    "kind": "write",
    "params": {
      "properties": {
        "agent": {
          "description": "Which specialist to run: 'goal_decomposer' (learning roadmaps), 'job_hunter' (resume tailoring / application pipeline), or 'linkedin_writer' (post drafts).",
          "title": "Agent",
          "type": "string"
        },
        "request": {
          "description": "What the user asked for, in their own words. Becomes the task's user request verbatim — never a summary, so the specialist sees the real ask.",
          "title": "Request",
          "type": "string"
        },
        "task_type": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional explicit action. Omit to let the specialist resolve intent from `request` — required for job_hunter, which is multi-action.",
          "title": "Task Type"
        },
        "session_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Session the request came from, for tracing.",
          "title": "Session Id"
        }
      },
      "required": [
        "agent",
        "request"
      ],
      "title": "RunSpecialistRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "status": {
          "title": "Status",
          "type": "string"
        },
        "agent": {
          "title": "Agent",
          "type": "string"
        },
        "task_type": {
          "title": "Task Type",
          "type": "string"
        },
        "output": {
          "default": "",
          "title": "Output",
          "type": "string"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "status",
        "agent",
        "task_type"
      ],
      "title": "RunSpecialistResponse",
      "type": "object"
    }
  },
  {
    "name": "mark_topic_done",
    "intent": "I finished that topic — what's next?",
    "description": "Mark a curriculum topic done (or started / skipped) and report which downstream topics it just unlocked. Python owns the DAG, so the frontier is computed here and never guessed.",
    "kind": "write",
    "params": {
      "properties": {
        "node": {
          "description": "The curriculum topic to update — its exact title or node id. Exact match only; I never guess at near-matches for a status change.",
          "title": "Node",
          "type": "string"
        },
        "roadmap": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Which roadmap (graph_id or exact title), when the node name alone is ambiguous. Omit it when the title is unique across roadmaps.",
          "title": "Roadmap"
        },
        "status": {
          "default": "done",
          "description": "'done' (he finished it), 'in_progress' (he started it), 'skipped' (deliberately skipped), or 'not_started' to undo a mistake.",
          "title": "Status",
          "type": "string"
        },
        "note": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional: what he actually did or found, in his words. Appended to the node's note as session evidence — never stored as an interpretation.",
          "title": "Note"
        },
        "session_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Session the update came from.",
          "title": "Session Id"
        }
      },
      "required": [
        "node"
      ],
      "title": "MarkTopicDoneRequest",
      "type": "object"
    },
    "result": {
      "properties": {
        "updated": {
          "title": "Updated",
          "type": "boolean"
        },
        "roadmap": {
          "default": "",
          "title": "Roadmap",
          "type": "string"
        },
        "roadmap_title": {
          "default": "",
          "title": "Roadmap Title",
          "type": "string"
        },
        "node": {
          "default": "",
          "title": "Node",
          "type": "string"
        },
        "node_title": {
          "default": "",
          "title": "Node Title",
          "type": "string"
        },
        "status": {
          "default": "",
          "title": "Status",
          "type": "string"
        },
        "unlocked": {
          "description": "Topics that this change just made study-ready (prerequisites now all done).",
          "items": {
            "type": "string"
          },
          "title": "Unlocked",
          "type": "array"
        },
        "remaining": {
          "default": 0,
          "title": "Remaining",
          "type": "integer"
        },
        "note_appended": {
          "default": false,
          "title": "Note Appended",
          "type": "boolean"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        }
      },
      "required": [
        "updated"
      ],
      "title": "MarkTopicDoneResponse",
      "type": "object"
    }
  },
  {
    "name": "learn_repo",
    "intent": "Teach me to understand this repo",
    "description": "Derive a prerequisite curriculum from a real codebase: which concepts the code embodies, where each one lives, and how hard it is. Citations are verified against the filesystem and unresolvable ones are reported, not trusted. Optionally saves it as a roadmap.",
    "kind": "write",
    "params": {
      "properties": {
        "repo_path": {
          "description": "Path to the repository to analyse. Must be inside the workspace sandbox (the student's own project, or a path under it).",
          "title": "Repo Path",
          "type": "string"
        },
        "title": {
          "default": "",
          "description": "Optional curriculum title; derived from the repo when empty.",
          "title": "Title",
          "type": "string"
        },
        "stopping_rule": {
          "default": "comprehension",
          "description": "'comprehension' — concepts needed to READ this repo (default). 'authorship' — concepts needed to REBUILD it. These produce very different curricula, so ask him which one he wants rather than guessing.",
          "title": "Stopping Rule",
          "type": "string"
        },
        "max_nodes": {
          "default": 10,
          "description": "Hard cap on concepts. Every concept has infinite prerequisites, so this is the stopping rule's teeth.",
          "maximum": 30,
          "minimum": 2,
          "title": "Max Nodes",
          "type": "integer"
        },
        "persist": {
          "default": false,
          "description": "False = read the inventory back and discuss it. True = also save it as a real roadmap he can study and mark topics complete against.",
          "title": "Persist",
          "type": "boolean"
        },
        "target_days": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional: spread it over N days.",
          "title": "Target Days"
        },
        "hours_per_day": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional: hours of study per day.",
          "title": "Hours Per Day"
        },
        "session_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Session the request came from.",
          "title": "Session Id"
        }
      },
      "required": [
        "repo_path"
      ],
      "title": "LearnRepoRequest",
      "type": "object"
    },
    "result": {
      "$defs": {
        "LearnedConcept": {
          "properties": {
            "concept": {
              "title": "Concept",
              "type": "string"
            },
            "difficulty": {
              "anyOf": [
                {
                  "type": "integer"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "title": "Difficulty"
            },
            "estimated_hours": {
              "default": 0.0,
              "title": "Estimated Hours",
              "type": "number"
            },
            "day": {
              "anyOf": [
                {
                  "type": "integer"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "title": "Day"
            },
            "anchors": {
              "description": "Where it lives: 'path::symbol'.",
              "items": {
                "type": "string"
              },
              "title": "Anchors",
              "type": "array"
            },
            "prerequisites": {
              "items": {
                "type": "string"
              },
              "title": "Prerequisites",
              "type": "array"
            },
            "what_to_cover": {
              "anyOf": [
                {
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "title": "What To Cover"
            }
          },
          "required": [
            "concept"
          ],
          "title": "LearnedConcept",
          "type": "object"
        }
      },
      "properties": {
        "ok": {
          "title": "Ok",
          "type": "boolean"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        },
        "repo": {
          "default": "",
          "title": "Repo",
          "type": "string"
        },
        "commit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Commit"
        },
        "files_scanned": {
          "default": 0,
          "title": "Files Scanned",
          "type": "integer"
        },
        "summary": {
          "default": "",
          "title": "Summary",
          "type": "string"
        },
        "roadmap": {
          "default": "",
          "title": "Roadmap",
          "type": "string"
        },
        "title": {
          "default": "",
          "title": "Title",
          "type": "string"
        },
        "concept_count": {
          "default": 0,
          "title": "Concept Count",
          "type": "integer"
        },
        "concepts": {
          "items": {
            "$ref": "#/$defs/LearnedConcept"
          },
          "title": "Concepts",
          "type": "array"
        },
        "anchor_coverage": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "How many cited locations actually resolve, e.g. '9/11 anchors resolved (82%)'.",
          "title": "Anchor Coverage"
        },
        "dropped_anchors": {
          "description": "Citations that did not resolve and were removed.",
          "items": {
            "type": "string"
          },
          "title": "Dropped Anchors",
          "type": "array"
        },
        "dropped_concepts": {
          "description": "Concepts cut to honour max_nodes.",
          "items": {
            "type": "string"
          },
          "title": "Dropped Concepts",
          "type": "array"
        },
        "broken_cycles": {
          "items": {
            "type": "string"
          },
          "title": "Broken Cycles",
          "type": "array"
        },
        "ground_truth_found": {
          "default": 0,
          "title": "Ground Truth Found",
          "type": "integer"
        },
        "ground_truth_total": {
          "default": 0,
          "title": "Ground Truth Total",
          "type": "integer"
        },
        "total_hours": {
          "default": 0.0,
          "title": "Total Hours",
          "type": "number"
        },
        "persisted": {
          "default": false,
          "title": "Persisted",
          "type": "boolean"
        },
        "roadmap_path": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Roadmap Path"
        }
      },
      "required": [
        "ok"
      ],
      "title": "LearnRepoResponse",
      "type": "object"
    }
  }
];
