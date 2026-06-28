package main

import (
	"database/sql"
	"encoding/json"

	_ "modernc.org/sqlite" // pure-Go SQLite (no cgo) — FTS5 included
)

// Node mirrors the columns of the Waystone `nodes` table we need for retrieval.
type Node struct {
	ID         string
	Fact       string
	Type       string
	Confidence float64
	Tags       []string
	Supersedes []string
}

// Store is a read-only handle on an existing Waystone context.db.
type Store struct{ db *sql.DB }

func OpenStore(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		return nil, err
	}
	return &Store{db: db}, nil
}

func (s *Store) Close() error { return s.db.Close() }

// NodeIDsByTags returns node ids whose normalized tag is in the given set
// (the `node_tags` index — the same entry-node lookup the Python retriever does).
func (s *Store) NodeIDsByTags(tags []string) (map[string]bool, error) {
	ids := map[string]bool{}
	if len(tags) == 0 {
		return ids, nil
	}
	q := "SELECT DISTINCT node_id FROM node_tags WHERE tag IN (" + placeholders(len(tags)) + ")"
	rows, err := s.db.Query(q, toAny(tags)...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids[id] = true
	}
	return ids, rows.Err()
}

// Neighbors expands the given ids one hop in BOTH edge directions.
func (s *Store) Neighbors(ids map[string]bool) (map[string]bool, error) {
	out := map[string]bool{}
	for id := range ids {
		out[id] = true
	}
	for id := range ids {
		rows, err := s.db.Query(
			"SELECT to_id FROM edges WHERE from_id = ? "+
				"UNION SELECT from_id FROM edges WHERE to_id = ?", id, id)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var nid string
			if err := rows.Scan(&nid); err != nil {
				rows.Close()
				return nil, err
			}
			out[nid] = true
		}
		rows.Close()
	}
	return out, nil
}

// Nodes fetches the node rows for the given ids.
func (s *Store) Nodes(ids map[string]bool) ([]Node, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	list := keys(ids)
	q := "SELECT id, fact, type, confidence, tags, supersedes FROM nodes " +
		"WHERE id IN (" + placeholders(len(list)) + ")"
	rows, err := s.db.Query(q, toAny(list)...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var nodes []Node
	for rows.Next() {
		var n Node
		var tagsJSON, supJSON string
		if err := rows.Scan(&n.ID, &n.Fact, &n.Type, &n.Confidence, &tagsJSON, &supJSON); err != nil {
			return nil, err
		}
		_ = json.Unmarshal([]byte(tagsJSON), &n.Tags)
		_ = json.Unmarshal([]byte(supJSON), &n.Supersedes)
		nodes = append(nodes, n)
	}
	return nodes, rows.Err()
}

// --- small helpers ---

func placeholders(n int) string {
	if n <= 0 {
		return ""
	}
	b := make([]byte, 0, 2*n)
	for i := 0; i < n; i++ {
		if i > 0 {
			b = append(b, ',')
		}
		b = append(b, '?')
	}
	return string(b)
}

func toAny[T any](in []T) []any {
	out := make([]any, len(in))
	for i, v := range in {
		out[i] = v
	}
	return out
}

func keys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
