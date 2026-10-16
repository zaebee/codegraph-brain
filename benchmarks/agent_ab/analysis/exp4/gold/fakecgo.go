package main

// loadFakeCgo is the platform-check loader, for a GOOS whose cgo this host cannot run (darwin on
// linux: no clang, no macOS SDK). It lists packages with `go list` (no -compiled, so cgo never
// runs), parses each package's Go and cgo files itself, and type-checks them with go/types'
// FakeImportC: `import "C"` declares an empty package and references to it are not errors.
//
//   - A package outside the module that imports "C" gets no SSA bodies, only its members' types
//     (a call into it is then a call to an external function, as for any body-less function).
//   - A function inside the module whose body names `C.` has that body replaced, before type
//     checking, by `panic("cgo")`. The function keeps its signature, so it is still a node, a
//     method and an implementation. Each elision is printed on stderr with the module functions
//     the original body called by name, so a lost edge cannot go unnoticed.
//
// The loader is validated by running it for GOOS=linux and comparing its dump with
// loadStandard's (see README): the platform check compares two dumps made the same way.

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"go/types"
	"os"
	"sort"
	"strings"

	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/ssa"
)

type importerFunc func(path string) (*types.Package, error)

func (f importerFunc) Import(path string) (*types.Package, error) { return f(path) }

func importsC(f *ast.File) bool {
	for _, im := range f.Imports {
		if im.Path.Value == `"C"` {
			return true
		}
	}
	return false
}

func namesC(n ast.Node) bool {
	found := false
	ast.Inspect(n, func(x ast.Node) bool {
		if sel, ok := x.(*ast.SelectorExpr); ok {
			if id, ok := sel.X.(*ast.Ident); ok && id.Name == "C" {
				found = true
			}
		}
		return !found
	})
	return found
}

func calledNames(n ast.Node) []string {
	set := map[string]bool{}
	ast.Inspect(n, func(x ast.Node) bool {
		if c, ok := x.(*ast.CallExpr); ok {
			switch fn := c.Fun.(type) {
			case *ast.Ident:
				set[fn.Name] = true
			case *ast.SelectorExpr:
				if id, ok := fn.X.(*ast.Ident); !ok || id.Name != "C" {
					set[fn.Sel.Name] = true
				}
			}
		}
		return true
	})
	var out []string
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// elideC drops, from a body's top-level statements, each one that names `C.` or uses a name a
// dropped statement declared, until nothing more drops. The rest keep their calls.
func elideC(list []ast.Stmt) (kept, dropped []ast.Stmt) {
	gone := map[string]bool{}
	uses := func(n ast.Node) bool {
		if namesC(n) {
			return true
		}
		hit := false
		ast.Inspect(n, func(x ast.Node) bool {
			if id, ok := x.(*ast.Ident); ok && gone[id.Name] {
				hit = true
			}
			return !hit
		})
		return hit
	}
	declared := func(st ast.Stmt) {
		ast.Inspect(st, func(x ast.Node) bool {
			switch d := x.(type) {
			case *ast.AssignStmt:
				if d.Tok == token.DEFINE {
					for _, l := range d.Lhs {
						if id, ok := l.(*ast.Ident); ok {
							gone[id.Name] = true
						}
					}
				}
			case *ast.ValueSpec:
				for _, id := range d.Names {
					gone[id.Name] = true
				}
			}
			return true
		})
	}
	rest := list
	for {
		kept, dropped = nil, nil
		changed := false
		for _, st := range rest {
			if uses(st) {
				dropped = append(dropped, st)
				before := len(gone)
				declared(st)
				changed = changed || len(gone) != before
			} else {
				kept = append(kept, st)
			}
		}
		if !changed {
			break
		}
	}
	// recompute against the final set so the split is consistent
	kept, dropped = nil, nil
	for _, st := range list {
		if uses(st) {
			dropped = append(dropped, st)
		} else {
			kept = append(kept, st)
		}
	}
	return kept, dropped
}

func loadFakeCgo(goarch string) (*ssa.Program, []*types.Package) {
	mode := packages.NeedName | packages.NeedFiles | packages.NeedImports | packages.NeedDeps | packages.NeedModule
	cfg := &packages.Config{Mode: mode, Dir: modDir, Tests: false, Env: os.Environ()}
	roots, err := packages.Load(cfg, "./...")
	if err != nil {
		panic(err)
	}
	var order []*packages.Package
	packages.Visit(roots, nil, func(p *packages.Package) { order = append(order, p) })

	fs := token.NewFileSet()
	typed := map[string]*types.Package{"unsafe": types.Unsafe}
	files := map[string][]*ast.File{}
	infos := map[string]*types.Info{}
	usesC := map[string]bool{}
	sizes := types.SizesFor("gc", goarch)
	bad := 0
	for _, p := range order {
		if p.PkgPath == "unsafe" {
			continue
		}
		for _, e := range p.Errors {
			fmt.Fprintln(os.Stderr, "LIST-ERROR", p.PkgPath, e)
			if inModPath(p.PkgPath) {
				bad++
			}
		}
		var syn []*ast.File
		for _, fn := range p.GoFiles {
			f, err := parser.ParseFile(fs, fn, nil, parser.ParseComments|parser.SkipObjectResolution)
			if err != nil {
				fmt.Fprintln(os.Stderr, "PARSE-ERROR", fn, err)
				bad++
				continue
			}
			if importsC(f) {
				usesC[p.PkgPath] = true
				if inModPath(p.PkgPath) {
					for _, d := range f.Decls {
						fd, ok := d.(*ast.FuncDecl)
						if !ok || fd.Body == nil || !namesC(fd.Body) {
							continue
						}
						kept, dropped := elideC(fd.Body.List)
						var dn []string
						for _, st := range dropped {
							dn = append(dn, calledNames(st)...)
						}
						sort.Strings(dn)
						fmt.Fprintf(os.Stderr, "CGO-ELIDED %s %s kept=%d dropped=%d dropped-calls=%v\n",
							fs.Position(fd.Pos()), fd.Name.Name, len(kept), len(dropped), dn)
						kept = append(kept, &ast.ExprStmt{X: &ast.CallExpr{Fun: ast.NewIdent("panic"),
							Args: []ast.Expr{&ast.BasicLit{Kind: token.STRING, Value: `"cgo"`}}}})
						fd.Body = &ast.BlockStmt{Lbrace: fd.Body.Lbrace, Rbrace: fd.Body.Rbrace, List: kept}
					}
				}
			}
			syn = append(syn, f)
		}
		info := &types.Info{
			Types:        map[ast.Expr]types.TypeAndValue{},
			Defs:         map[*ast.Ident]types.Object{},
			Uses:         map[*ast.Ident]types.Object{},
			Implicits:    map[ast.Node]types.Object{},
			Instances:    map[*ast.Ident]types.Instance{},
			Scopes:       map[ast.Node]*types.Scope{},
			Selections:   map[*ast.SelectorExpr]*types.Selection{},
			FileVersions: map[*ast.File]string{},
		}
		var errs []error
		tc := &types.Config{
			Importer: importerFunc(func(path string) (*types.Package, error) {
				if ip, ok := p.Imports[path]; ok {
					path = ip.PkgPath
				}
				if t, ok := typed[path]; ok {
					return t, nil
				}
				return nil, fmt.Errorf("not yet typed: %s", path)
			}),
			FakeImportC: true,
			Sizes:       sizes,
			Error:       func(err error) { errs = append(errs, err) },
		}
		if p.Module != nil && p.Module.GoVersion != "" {
			tc.GoVersion = "go" + p.Module.GoVersion
		}
		tp, _ := tc.Check(p.PkgPath, fs, syn, info)
		for _, e := range errs {
			msg := e.Error()
			if inModPath(p.PkgPath) || !usesC[p.PkgPath] {
				// an unused import or variable left by an elided cgo statement is expected
				if inModPath(p.PkgPath) && usesC[p.PkgPath] && (strings.Contains(msg, "imported and not used") || strings.Contains(msg, "declared and not used")) {
					fmt.Fprintln(os.Stderr, "TYPE-NOTE", p.PkgPath, msg)
					continue
				}
				fmt.Fprintln(os.Stderr, "TYPE-ERROR", p.PkgPath, msg)
				bad++
			}
		}
		typed[p.PkgPath] = tp
		files[p.PkgPath] = syn
		infos[p.PkgPath] = info
	}
	if bad > 0 {
		fmt.Fprintln(os.Stderr, "fakecgo load errors:", bad)
		os.Exit(1)
	}
	prog := ssa.NewProgram(fs, ssa.InstantiateGenerics)
	prog.CreatePackage(types.Unsafe, nil, nil, true)
	var mod []*types.Package
	for _, p := range order {
		if p.PkgPath == "unsafe" {
			continue
		}
		if usesC[p.PkgPath] && !inModPath(p.PkgPath) {
			fmt.Fprintln(os.Stderr, "NO-BODIES", p.PkgPath)
			prog.CreatePackage(typed[p.PkgPath], nil, nil, true)
		} else {
			prog.CreatePackage(typed[p.PkgPath], files[p.PkgPath], infos[p.PkgPath], true)
		}
		if inModPath(p.PkgPath) {
			mod = append(mod, typed[p.PkgPath])
		}
		for _, im := range typed[p.PkgPath].Imports() {
			if im.Path() == "C" && prog.ImportedPackage("C") == nil && !usesC["C:"+p.PkgPath] {
				// FakeImportC's empty "C"; one per importer, so create each under its own name
				usesC["C:"+p.PkgPath] = true
				if prog.Package(im) == nil {
					prog.CreatePackage(im, nil, nil, false)
				}
			}
		}
	}
	prog.Build()
	return prog, mod
}
