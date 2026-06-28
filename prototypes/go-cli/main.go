// Command waystone (Go prototype) — a single static binary that reads an
// existing Waystone SQLite graph and serves the default retrieval path with
// zero C dependencies. See DESIGN.md.
package main

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

var version = "0.1.0-proto"

func main() {
	root := &cobra.Command{
		Use:           "waystone",
		Short:         "Waystone — context intelligence (Go prototype)",
		SilenceUsage:  true,
		SilenceErrors: true,
	}

	root.AddCommand(&cobra.Command{
		Use:   "version",
		Short: "Print the version",
		Run: func(cmd *cobra.Command, args []string) {
			fmt.Println("waystone (go prototype)", version)
		},
	})
	root.AddCommand(newQueryCmd())

	if err := root.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
