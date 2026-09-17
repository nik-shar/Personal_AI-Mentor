import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agents.Job_Hunter.job_hunter import run_job_hunter
from schemas import AgentTask, MemorySlice


def run_test():
    # Construct a mock AgentTask
    mem_slice = MemorySlice(
        agent_name="job_hunter",
        task_type="search_jobs",
        relevant_profile={
            "target_roles": ["AI Engineer", "Machine Learning Engineer"],
            "target_locations": ["Remote", "India"]
        },
        private_memory={}
    )
    
    task = AgentTask(
        task_id="test-job-search-123",
        agent_name="job_hunter",
        task_type="search_jobs",
        instructions="Find me remote AI Engineer jobs",
        source="chat",
        memory_slice=mem_slice
    )
    
    print("Running job_hunter agent with search_jobs task...")
    result = run_job_hunter(task)
    
    print("\n--- Output ---")
    print(result.output)
    
    print("\n--- Memory Delta ---")
    if result.memory_delta:
        # Cache lives under the registered agent name so the orchestrator's
        # memory_merger → context_builder round-trip resolves "#N" references.
        cache = (
            result.memory_delta.get("agent_private_memory", {})
            .get("job_hunter", {})
            .get("job_hunter_search_cache", {})
        )
        print(f"Cache contains {len(cache)} jobs.")
        for k, v in cache.items():
            print(f"#{k}: {v.get('title')} @ {v.get('company')}")
    else:
        print("No memory delta returned.")

if __name__ == "__main__":
    run_test()
