package main

import (
	"fmt"
	"sort"
	"strings"

	"github.com/spf13/cobra"
)

// A tiny stop-word set, mirroring the spirit of the Python keyword extractor.
var stopwords = map[string]bool{
	"the": true, "a": true, "an": true, "and": true, "or": true, "of": true,
	"to": true, "in": true, "on": true, "for": true, "is": true, "are": true,
	"was": true, "were": true, "be": true, "with": true, "as": true, "by": true,
	"at": true, "it": true, "this": true, "that": true, "we": true,
	"how": true, "what": true, "do": true, "does": true, "did": true, "our": true,
}

// keywords lowercases, splits on non-alphanumeric (keeping hyphens), and drops
// stop-words and short tokens.
func keywords(task string) []string {
	isSep := func(r rune) bool {
		return !((r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') ||
			(r >= '0' && r <= '9') || r == '-')
	}
	var out []string
	seen := map[string]bool{}
	for _, w := range strings.FieldsFunc(strings.ToLower(task), isSep) {
		if len(w) < 3 || stopwords[w] || seen[w] {
			continue
		}
		seen[w] = true
		out = append(out, w)
	}
	return out
}

// type_order from the Python retriever (session_summary first).
var typeOrder = []string{
	"session_summary", "decision", "transition", "constraint",
	"implementation", "resolved", "preference", "question", "lesson_learned",
}

func newQueryCmd() *cobra.Command {
	var dbPath string
	var hops, topK int

	cmd := &cobra.Command{
		Use:   "query [task...]",
		Short: "Retrieve relevant facts for a task (default read path, pure Go)",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			task := strings.Join(args, " ")
			store, err := OpenStore(dbPath)
			if err != nil {
				return err
			}
			defer store.Close()

			kw := keywords(task)
			frontier, err := store.NodeIDsByTags(kw)
			if err != nil {
				return err
			}
			for h := 0; h < hops; h++ {
				if frontier, err = store.Neighbors(frontier); err != nil {
					return err
				}
			}
			nodes, err := store.Nodes(frontier)
			if err != nil {
				return err
			}

			nodes = dropSuperseded(nodes)
			sort.SliceStable(nodes, func(i, j int) bool {
				return nodes[i].Confidence > nodes[j].Confidence
			})
			if len(nodes) > topK {
				nodes = nodes[:topK]
			}
			fmt.Print(format(task, kw, nodes))
			return nil
		},
	}
	cmd.Flags().StringVar(&dbPath, "db", "", "path to a Waystone context.db (required)")
	cmd.Flags().IntVar(&hops, "hops", 2, "BFS depth")
	cmd.Flags().IntVar(&topK, "top-k", 10, "max nodes returned")
	_ = cmd.MarkFlagRequired("db")
	return cmd
}

// dropSuperseded removes nodes listed in another node's `supersedes` array
// (the superseded_pruning strategy).
func dropSuperseded(nodes []Node) []Node {
	superseded := map[string]bool{}
	for _, n := range nodes {
		for _, s := range n.Supersedes {
			superseded[s] = true
		}
	}
	out := nodes[:0]
	for _, n := range nodes {
		if !superseded[n.ID] {
			out = append(out, n)
		}
	}
	return out
}

func format(task string, kw []string, nodes []Node) string {
	var b strings.Builder
	fmt.Fprintf(&b, "# Context for: %s\n", task)
	fmt.Fprintf(&b, "_keywords: %s · %d nodes_\n\n", strings.Join(kw, ", "), len(nodes))
	byType := map[string][]Node{}
	for _, n := range nodes {
		byType[n.Type] = append(byType[n.Type], n)
	}
	for _, t := range typeOrder {
		group := byType[t]
		if len(group) == 0 {
			continue
		}
		fmt.Fprintf(&b, "## %s\n", t)
		for _, n := range group {
			fmt.Fprintf(&b, "- %s\n", n.Fact)
		}
		b.WriteString("\n")
	}
	return b.String()
}
