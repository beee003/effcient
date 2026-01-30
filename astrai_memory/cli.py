#!/usr/bin/env python3
"""
Astrai Memory CLI

Usage:
    python -m astrai_memory remember "User likes dark mode" --category preference
    python -m astrai_memory recall "What are user preferences?"
    python -m astrai_memory list
    python -m astrai_memory stats
"""

import argparse
import json
import sys
from datetime import datetime

from .memory import AstraiMemory


def format_time(timestamp_ms: int) -> str:
    """Format timestamp for display"""
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M")


def cmd_remember(args, memory: AstraiMemory):
    """Store a new memory"""
    memory_id = memory.remember(
        content=args.content,
        category=args.category,
        source=args.source,
    )
    print(f"Remembered [{memory_id}]: {args.content[:50]}...")


def cmd_recall(args, memory: AstraiMemory):
    """Search memories"""
    results = memory.recall(
        query=args.query,
        limit=args.limit,
        category=args.category,
    )

    if not results:
        print("No memories found.")
        return

    print(f"\nFound {len(results)} memories:\n")
    for i, mem in enumerate(results, 1):
        score_bar = "" * int(mem["score"] * 10) + "" * (10 - int(mem["score"] * 10))
        print(f"{i}. [{mem['id']}] {score_bar} {mem['score']:.2f}")
        print(f"   {mem['content'][:80]}{'...' if len(mem['content']) > 80 else ''}")
        print(f"   Category: {mem['category']} | {format_time(mem['created_at'])}")
        print()


def cmd_list(args, memory: AstraiMemory):
    """List all memories"""
    memories = memory.list_memories(
        category=args.category,
        limit=args.limit,
    )

    if not memories:
        print("No memories stored yet.")
        return

    print(f"\nMemories ({len(memories)}):\n")
    for mem in memories:
        print(f"[{mem['id']}] {mem['category']:12} | {format_time(mem['created_at'])}")
        print(f"    {mem['content'][:70]}{'...' if len(mem['content']) > 70 else ''}")
        print()


def cmd_forget(args, memory: AstraiMemory):
    """Delete a memory"""
    if memory.forget(args.id):
        print(f"Forgot memory [{args.id}]")
    else:
        print(f"Memory [{args.id}] not found")


def cmd_stats(args, memory: AstraiMemory):
    """Show memory statistics"""
    stats = memory.stats()

    print("\n Astrai Memory Stats\n")
    print(f"Total memories:  {stats['total_memories']}")
    print(f"Cache size:      {stats['embedding_cache_size']} embeddings")
    print(f"Database:        {stats['db_path']}")

    if stats['categories']:
        print(f"\nCategories:")
        for cat, count in stats['categories'].items():
            print(f"  {cat}: {count}")
    print()


def cmd_export(args, memory: AstraiMemory):
    """Export memories to JSON"""
    memories = memory.list_memories(limit=10000)

    output = {
        "version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "memories": memories,
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Exported {len(memories)} memories to {args.output}")
    else:
        print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Astrai Memory - Local-first AI memory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", help="Path to database file")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # remember
    p_remember = subparsers.add_parser("remember", help="Store a new memory")
    p_remember.add_argument("content", help="Content to remember")
    p_remember.add_argument("-c", "--category", default="general", help="Category")
    p_remember.add_argument("-s", "--source", default="user", help="Source")

    # recall
    p_recall = subparsers.add_parser("recall", help="Search memories")
    p_recall.add_argument("query", help="Search query")
    p_recall.add_argument("-l", "--limit", type=int, default=5, help="Max results")
    p_recall.add_argument("-c", "--category", help="Filter by category")

    # list
    p_list = subparsers.add_parser("list", help="List all memories")
    p_list.add_argument("-l", "--limit", type=int, default=20, help="Max results")
    p_list.add_argument("-c", "--category", help="Filter by category")

    # forget
    p_forget = subparsers.add_parser("forget", help="Delete a memory")
    p_forget.add_argument("id", help="Memory ID to delete")

    # stats
    subparsers.add_parser("stats", help="Show statistics")

    # export
    p_export = subparsers.add_parser("export", help="Export memories to JSON")
    p_export.add_argument("-o", "--output", help="Output file (default: stdout)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Initialize memory
    memory = AstraiMemory(db_path=args.db)

    try:
        if args.command == "remember":
            cmd_remember(args, memory)
        elif args.command == "recall":
            cmd_recall(args, memory)
        elif args.command == "list":
            cmd_list(args, memory)
        elif args.command == "forget":
            cmd_forget(args, memory)
        elif args.command == "stats":
            cmd_stats(args, memory)
        elif args.command == "export":
            cmd_export(args, memory)
    finally:
        memory.close()


if __name__ == "__main__":
    main()
