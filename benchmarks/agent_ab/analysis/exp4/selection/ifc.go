// ifc: in a module's non-test packages, count interfaces with >=2 in-module implementations,
// and files that declare type parameters. Usage: ifc <moduleDir> <modulePath>
package main

import (
	"fmt"
	"go/ast"
	"go/types"
	"os"
	"strings"

	"golang.org/x/tools/go/packages"
)

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage: ifc <moduleDir> <modulePath>")
		os.Exit(2)
	}
	dir, mod := os.Args[1], os.Args[2]
	cfg := &packages.Config{Mode: packages.LoadAllSyntax, Dir: dir, Tests: false}
	pkgs, err := packages.Load(cfg, "./...")
	if err != nil {
		fmt.Println("LOADERR", err)
		os.Exit(1)
	}
	var ifaces []*types.Named
	var concrete []*types.Named
	files, generic := 0, 0
	for _, p := range pkgs {
		if !strings.HasPrefix(p.PkgPath, mod) || p.Types == nil {
			continue
		}
		for _, f := range p.Syntax {
			files++
			found := false
			ast.Inspect(f, func(n ast.Node) bool {
				switch x := n.(type) {
				case *ast.FuncDecl:
					if x.Type.TypeParams != nil && len(x.Type.TypeParams.List) > 0 {
						found = true
					}
				case *ast.TypeSpec:
					if x.TypeParams != nil && len(x.TypeParams.List) > 0 {
						found = true
					}
				}
				return !found
			})
			if found {
				generic++
			}
		}
		for _, name := range p.Types.Scope().Names() {
			tn, ok := p.Types.Scope().Lookup(name).(*types.TypeName)
			if !ok || tn.IsAlias() {
				continue
			}
			nt, ok := tn.Type().(*types.Named)
			if !ok || nt.TypeParams().Len() > 0 {
				continue
			}
			if it, ok := nt.Underlying().(*types.Interface); ok {
				if it.NumMethods() > 0 {
					ifaces = append(ifaces, nt)
				}
			} else {
				concrete = append(concrete, nt)
			}
		}
	}
	multi := 0
	for _, i := range ifaces {
		it := i.Underlying().(*types.Interface)
		n := 0
		for _, c := range concrete {
			if types.Implements(c, it) || types.Implements(types.NewPointer(c), it) {
				n++
			}
		}
		if n >= 2 {
			multi++
		}
	}
	fmt.Printf("files=%d generic_files=%d ifaces=%d multi_impl=%d\n", files, generic, len(ifaces), multi)
}
