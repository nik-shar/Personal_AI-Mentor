Your current architecture is data-driven — the UserStateManager needs 14+ days
  of schedule events before it even populates peak_hours. The MentorMetaPattern
  system needs weeks of behavior to detect patterns. The mood classifier needs 3+
  past conversations.

  So on Day 1, when you say something like:
  │ "I've been mass-applying to jobs for 3 months with no callbacks. I know I need
  │ to upskill but I can't bring myself to start. I just feel stuck."
  The Mentor currently has zero context — no streak data, no mood history, no
  drift score. It's psychologically deaf. And this is exactly the moment where the
  response matters most, because first impressions build or destroy trust.
  ──────
  ## Two Separate Problems Hidden in One

  I see two distinct challenges here:

  ### 1. Intelligent First-Contact Handling (Cold Start)
  How does the Mentor respond well before it has data?
  ### 2. Ongoing Psychological Calibration (Warm State)
  How does the Mentor get progressively smarter about your specific emotional
  patterns over time?

  Your existing architecture handles #2 reasonably well (mood tracking, meta-
  patterns, personality system). The gap is almost entirely in #1.
  ──────
  ## How I'd Think About Solving #1
  There are a few layers to this:
  ### Layer A: Acknowledge Before Acting

  The biggest mistake an AI mentor makes is jumping straight to "Here's your study
  plan!" when someone just told you they're burned out. The Mentor's reasoner
  prompt needs a pre-routing emotional checkpoint:

    Before deciding what AGENT to dispatch, ask:
      → Is the user sharing an emotional state or personal struggle?
      → If YES: Your FIRST response must acknowledge the emotion.
         Do NOT route to an agent. Do NOT offer a plan.
         Respond as a human would: validate, reflect, ask a follow-up.
      → Only AFTER the user signals readiness ("okay, let's plan" / "what should I
  do")
         should you transition to the professional/agent side.
    
  This is a behavioral rule, not a data requirement — it works on Day 1 with zero
  history.
  ### Layer B: Structured Onboarding Conversation (not a form)

  Instead of a profile YAML or an onboard_profile.py script, what if the first
  interaction is a guided but natural conversation? Not "fill in these fields" but
  something like:

  │ "Hey Nikhil. Before we start building plans, I want to actually understand
  │ where you're at. Not just your goals — but how you're feeling about them.
  │ What's your situation right now?"

  Then the Mentor listens, and from that single conversation, it extracts:
   Signal                   | What it infers           | How it stores it
  --------------------------|--------------------------|--------------------------
   "mass-applying for 3     | Job search fatigue,      | profile_facts:
   months"                  | possible frustration     | situation.job_search_dur
                            |                          | ation
   "can't bring myself to   | Low motivation, possible | initial_mood:
   start"                   | overwhelm                | frustrated,
                            |                          | coaching_emphasis:
                            |                          | recovery
   "I feel stuck"           | Emotional disclosure =   | accountability_level: 3-
                            | user wants empathy, not  | 4 (start gentle)
                            | just plans               |
  This is essentially a one-time LLM extraction from the onboarding conversation
  that seeds the mentor_personality and user_state with reasonable defaults — not
  computed from behavioral data, but inferred from the user's own words.
  ### Layer C: Default Personality Stance = Conservative + Warm
  Right now, the default accountability_level is 5 and tone is "direct". For a
  cold-start scenario, I'd argue the defaults should be:
   Field                | Current Default | Better Cold-S… | Why
  ----------------------|-----------------|----------------|----------------------
   accountability_level | 5               | 3              | Earn the right to
                        |                 |                | push. Start gentle.
   tone                 | "direct"        | "warm"         | Direct tone with
                        |                 |                | zero context can
                        |                 |                | feel dismissive
   coaching_emphasis    | "balance"       | "consistency"  | Small wins build
                        |                 |                | trust
   humor                | false           | false          | Correct — humor
                        |                 |                | without rapport is
                        |                 |                | risky

  The Mentor escalates accountability as it builds data and the relationship
  deepens. It doesn't start at the midpoint.

  ### Layer D: Emotional Disclosure Categories
  Not all personal sharing is the same. The Mentor needs to differentiate:
   Category           | Example                     | Appropriate Response
  --------------------|-----------------------------|-----------------------------
   Venting            | "Today was garbage, nothing | Mirror + validate. Don't
                      | went right"                 | problem-solve.
   Seeking guidance   | "I don't know if I should   | Professional mode —
                      | focus on DSA or system      | analyze, suggest.
                      | design"                     |
   Crisis signal      | "I don't see the point      | Acknowledge seriously.
                      | anymore"                    | Don't play therapist.
                      |                             | Gently suggest professional
                      |                             | support.
   Context sharing    | "I'm between jobs right     | Store as profile fact.
                      | now"                        | Adapt plans accordingly.
   Burnout disclosure | "I'm exhausted and nothing  | Switch to
                      | excites me"                 | coaching_emphasis:
                      |                             | recovery. Reduce plan
                      |                             | intensity.
  This classification could be a lightweight LLM call (similar to your mood
  classifier) that runs before the main reasoner, tagging the user's message with
  a disclosure_type.
  ──────
  ## The Professional Boundary Question

  This is the hardest part. Your CLAUDE.md already says:

  │ "Earn trust in low-stakes domains before high-stakes ones. Mindset/goals
  │ coaching carries real risk if done badly."

  That's wise. But the user might start with a high-stakes disclosure on Day 1. My
  take:

  • The Mentor should never pretend to be a therapist. It's a
  learning/productivity mentor that happens to be emotionally aware.
  • It can say things like: "That sounds really draining. Before we talk about
  study plans — is there anything else going on that I should know about, so I can
  set realistic expectations?"
  • It cannot say things like: "It sounds like you might be experiencing symptoms
  of depression."
  • The boundary: acknowledge the emotion, adapt the plan, but don't diagnose or
  treat.
  ──────
  ## Putting It Together — What Would Change Architecturally?

  If you wanted to implement this, here's where it touches the existing system:

  1. New module: orchestrator/cognition/onboarding.py — handles the first 1-3
  conversations differently (guided discovery mode)
  2. Reasoner prompt update: Add the "emotional checkpoint" before agent routing
  3. New field in situation report: disclosure_type classification
  4. Default personality shift: Lower accountability, warmer tone on cold start
  5. Profile seeding from conversation: LLM extraction of situational context from
  the onboarding chat, stored as profile_facts
