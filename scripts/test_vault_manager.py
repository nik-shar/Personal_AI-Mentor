"""
scripts/test_vault_manager.py

Automated verification suite for Vault Management System:
- Create Roadmap
- List Vault Roadmaps
- Edit Node / Add Node to Graph
- Delete Single Node
- Delete Entire Graph
"""

import sys
from pathlib import Path

# Ensure root workspace directory is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv()

from orchestrator.config import OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER
from orchestrator.memory.obsidian_graph import (
    load_topic_graphs,
    list_vault_roadmaps,
    edit_topic_node,
    delete_topic_node,
    delete_topic_graph,
    write_topic_node,
    write_roadmap_index,
)
from agents.Goal_Decomposer.goal_decomposer import app as goal_decomposer_app, build_initial_state
from schemas import AgentTask, MemorySlice, ResultStatus, TaskSource
from schemas.memory import TopicNode

def test_vault_crud():
    print("=== Testing Obsidian Vault Manager CRUD Operations ===")

    graph_id = "test_quant_serving"
    graph_title = "Test Quantization & Serving"

    # 1. Create test topic nodes
    node1 = TopicNode(id="tn_vllm_intro", title="vLLM Intro & Architecture", estimated_hours=2.0, status="not_started")
    node2 = TopicNode(id="tn_awq_quant", title="AWQ Model Quantization", estimated_hours=2.5, status="not_started", prerequisites=[node1.id])

    write_topic_node(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, graph_id, graph_title, node1, day=1, prereq_titles=[])
    write_topic_node(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, graph_id, graph_title, node2, day=2, prereq_titles=[node1.title])

    nodes_by_day = {
        1: [(node1, [])],
        2: [(node2, [node1.title])],
    }
    write_roadmap_index(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, graph_id, graph_title, nodes_by_day)

    print("✅ 1. Created initial test roadmap and nodes in vault")

    # 2. List Vault Roadmaps
    roadmaps = list_vault_roadmaps(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
    found = next((r for r in roadmaps if r["topic_id"] == graph_id), None)
    assert found is not None, "Created roadmap must be found by list_vault_roadmaps"
    assert found["total_nodes"] == 2, f"Expected 2 nodes, found {found['total_nodes']}"
    print(f"✅ 2. Listed roadmaps: Found '{found['title']}' with {found['total_nodes']} nodes")

    # 3. Edit Topic Node (Update status and title)
    ok_edit, edit_msg = edit_topic_node(
        OBSIDIAN_VAULT_PATH,
        OBSIDIAN_TOPIC_FOLDER,
        "vLLM Intro & Architecture",
        {"status": "completed", "estimated_hours": 2.5},
    )
    assert ok_edit, f"Edit should succeed: {edit_msg}"

    graphs_after_edit = load_topic_graphs(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
    target_g = next((g for g in graphs_after_edit if g.topic_id == graph_id), None)
    assert target_g is not None
    updated_n = target_g.nodes.get(node1.id)
    assert updated_n is not None
    assert updated_n.status == "done", f"Status should be done, got {updated_n.status}"
    print("✅ 3. Edited node properties (status=done, hours=2.5)")

    # 4. Agent Intent Execution for List & Delete
    list_task = AgentTask(
        task_id="test_list_task",
        source=TaskSource.CHAT,
        agent_name="goal_decomposer",
        task_type="decompose_goal",
        instructions="List my vault roadmaps",
        params={"action_type": "list"},
        memory_slice=MemorySlice(agent_name="goal_decomposer", task_type="decompose_goal", relevant_profile={}),
    )
    out_list = goal_decomposer_app.invoke(build_initial_state(list_task))
    assert out_list["result"].status == ResultStatus.SUCCESS, "List action must return SUCCESS"
    assert "Test Quantization & Serving" in out_list["result"].output, "List output must include test roadmap title"
    print("✅ 4. Goal Decomposer 'list' action executed via agent workflow")

    # 5. Delete Single Node
    ok_del_node, del_msg = delete_topic_node(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, "AWQ Model Quantization")
    assert ok_del_node, f"Delete node should succeed: {del_msg}"
    roadmaps_after_del = list_vault_roadmaps(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
    found_after_del = next((r for r in roadmaps_after_del if r["topic_id"] == graph_id), None)
    assert found_after_del is not None and found_after_del["total_nodes"] == 1, "Node count should drop to 1"
    print("✅ 5. Deleted single topic node from vault")

    # 6. Delete Entire Graph
    ok_del_graph, count_deleted, deleted_files = delete_topic_graph(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, graph_id)
    assert ok_del_graph, "Delete graph should succeed"
    roadmaps_final = list_vault_roadmaps(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
    found_final = next((r for r in roadmaps_final if r["topic_id"] == graph_id), None)
    assert found_final is None, "Graph must be completely removed from vault"
    print(f"✅ 6. Deleted entire graph from vault (removed {count_deleted} file(s))")

    print("\n🎉 ALL VAULT MANAGER CRUD VERIFICATION TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_vault_crud()
