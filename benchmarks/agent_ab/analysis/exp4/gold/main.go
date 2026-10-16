// exp4cg dumps the call graphs exp4's gold is built from: go/ssa CHA and VTA over a Go module's
// non-test packages, for the GOOS/GOARCH in the environment.
//
// Usage: GOOS=linux GOARCH=amd64 exp4cg <moduleDir> <modulePath> > dump.tsv
//
// Every function is named by a key that folds what the gold model folds:
//
//	<pkg>.<Func>            a function; <pkg> is the package path relative to the module
//	<pkg>.<Type>.<Method>   a method, pointer or value receiver alike
//
// Closures fold into their outermost enclosing function, `$bound` and `$thunk` wrappers into
// their method, and generic instances into their origin. Promoted-method wrappers stay separate
// nodes, keyed `synth:<ssa name>`, so they are traversed but are never gold symbols.
//
// Output, tab-separated and sorted (so the same input gives the same bytes):
//
//	H  goos goarch modulePath
//	F  key file line flags            flags: synth (promoted wrapper), pkginit (the synthetic
//	                                  package initialiser), generic, testfile, main (package main)
//	E  graph caller callee kind site  graph cha|vta; kind static|go|defer|dynamic|invoke
//	                                  site file:line; caller and callee both in the module
//	I  graph caller callee iface scope site   one per invoke edge: the interface type and
//	                                          its scope inmod|std|ext|anon
//	R  taker callee site how          a function value of an in-module function taken
//	                                  (how: func|bound|thunk|iface:<iface>); a closure's
//	                                  own body is not a reference
//	A  taker callee site launcher     a function value passed straight to a goroutine
//	                                  launcher (sync.WaitGroup.Go, errgroup.Group.Go/TryGo,
//	                                  time.AfterFunc, or an in-module function that `go`-calls
//	                                  that parameter)
package main

import (
	"fmt"
	"go/token"
	"go/types"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"golang.org/x/tools/go/callgraph"
	"golang.org/x/tools/go/callgraph/cha"
	"golang.org/x/tools/go/callgraph/vta"
	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/ssa"
	"golang.org/x/tools/go/ssa/ssautil"
)

var (
	modDir, modPath string
	fset            *token.FileSet
	out             []string
	modTypes        []*types.Named // in-module named non-interface types, for interface method values
)

func emit(format string, a ...any) { out = append(out, fmt.Sprintf(format, a...)) }

func inModPath(path string) bool { return path == modPath || strings.HasPrefix(path, modPath+"/") }

func relPkg(path string) string {
	if path == modPath {
		return "."
	}
	return strings.TrimPrefix(path, modPath+"/")
}

func top(f *ssa.Function) *ssa.Function {
	for f.Parent() != nil {
		f = f.Parent()
	}
	if f.Origin() != nil {
		f = f.Origin()
	}
	return f
}

// objOf is the declared function or method a (top-level) SSA function stands for, or nil.
func objOf(f *ssa.Function) *types.Func {
	if o, ok := f.Object().(*types.Func); ok {
		return o.Origin()
	}
	return nil
}

func inMod(f *ssa.Function) bool {
	f = top(f)
	if f.Pkg != nil {
		return inModPath(f.Pkg.Pkg.Path())
	}
	if o := objOf(f); o != nil && o.Pkg() != nil {
		return inModPath(o.Pkg().Path())
	}
	return false
}

// isWrapper: a promoted-method (or pointer-to-value) wrapper. Package initialisers, `$bound`,
// `$thunk` and generic instances are synthetic too, but they fold or are named on their own.
func isWrapper(f *ssa.Function) bool { return strings.HasPrefix(f.Synthetic, "wrapper for") }

func recvName(t types.Type) string {
	if p, ok := t.(*types.Pointer); ok {
		t = p.Elem()
	}
	switch n := t.(type) {
	case *types.Named:
		return n.Obj().Name()
	case *types.Alias:
		return n.Obj().Name()
	}
	return types.TypeString(t, func(*types.Package) string { return "" })
}

func funcKey(o *types.Func) string {
	pkg := relPkg(o.Pkg().Path())
	sig := o.Type().(*types.Signature)
	if r := sig.Recv(); r != nil {
		return pkg + "." + recvName(r.Type()) + "." + o.Name()
	}
	return pkg + "." + o.Name()
}

// key names a function as the gold model sees it.
func key(f *ssa.Function) string {
	f = top(f)
	if isWrapper(f) {
		return "synth:" + strings.ReplaceAll(f.String(), modPath+"/", "")
	}
	if o := objOf(f); o != nil && o.Pkg() != nil && f.Synthetic == "" && f.Signature.Recv() == nil && o.Name() == "init" {
		// a user-written `func init()`: SSA names it init#N; keep that, so it never collides
		// with the synthetic package initialiser `init` or with another file's init
		return relPkg(f.Pkg.Pkg.Path()) + "." + f.Name()
	}
	if o := objOf(f); o != nil && o.Pkg() != nil {
		return funcKey(o)
	}
	// package initialisers and other unnamed functions
	return relPkg(f.Pkg.Pkg.Path()) + "." + f.Name()
}

func posOf(p token.Pos) (string, int) {
	if !p.IsValid() {
		return "", 0
	}
	pp := fset.Position(p)
	r, err := filepath.Rel(modDir, pp.Filename)
	if err != nil {
		r = pp.Filename
	}
	return r, pp.Line
}

func site(p token.Pos) string {
	f, l := posOf(p)
	return fmt.Sprintf("%s:%d", f, l)
}

// siteIn: an instruction's position, or its function's when the instruction has none
// (operands of compiler-made instructions, such as a method value's closure).
func siteIn(p token.Pos, fn *ssa.Function) string {
	if !p.IsValid() {
		p = fn.Pos()
	}
	return site(p)
}

func ifaceScope(t types.Type) (string, string) {
	name := types.TypeString(t, func(p *types.Package) string {
		if inModPath(p.Path()) {
			return relPkg(p.Path())
		}
		return p.Path()
	})
	switch n := types.Unalias(t).(type) {
	case *types.Named:
		pkg := n.Obj().Pkg()
		switch {
		case pkg == nil:
			return name, "std" // error
		case inModPath(pkg.Path()):
			return name, "inmod"
		case !strings.Contains(strings.SplitN(pkg.Path(), "/", 2)[0], "."):
			return name, "std"
		default:
			return name, "ext"
		}
	}
	return name, "anon"
}

func kindOf(s ssa.CallInstruction) string {
	c := s.Common()
	switch {
	case c.IsInvoke():
		return "invoke"
	case c.StaticCallee() == nil:
		return "dynamic"
	}
	switch s.(type) {
	case *ssa.Go:
		return "go"
	case *ssa.Defer:
		return "defer"
	}
	return "static"
}

// launcherParam reports whether in-module function g starts its parameter i on a goroutine:
// `go p()`, or `go func() { ... p() ... }()` with p captured.
func launcherParam(g *ssa.Function, i int) bool {
	if g == nil || !inMod(g) || g.Parent() != nil || i >= len(g.Params) {
		return false
	}
	p := g.Params[i]
	for _, b := range g.Blocks {
		for _, ins := range b.Instrs {
			gs, ok := ins.(*ssa.Go)
			if !ok {
				continue
			}
			if gs.Call.Value == p {
				return true
			}
			mc, ok := gs.Call.Value.(*ssa.MakeClosure)
			if !ok {
				continue
			}
			cl := mc.Fn.(*ssa.Function)
			for k, bnd := range mc.Bindings {
				if bnd != p {
					continue
				}
				fv := cl.FreeVars[k]
				for _, cb := range cl.Blocks {
					for _, ci := range cb.Instrs {
						if c, ok := ci.(ssa.CallInstruction); ok && c.Common().Value == fv {
							return true
						}
					}
				}
			}
		}
	}
	return false
}

func isStdLauncher(c *ssa.CallCommon) string {
	g := c.StaticCallee()
	if g == nil {
		return ""
	}
	s := g.String()
	switch s {
	case "(*sync.WaitGroup).Go", "(*golang.org/x/sync/errgroup.Group).Go", "(*golang.org/x/sync/errgroup.Group).TryGo",
		"time.AfterFunc": // runs f in its own goroutine once the timer fires
		return s
	}
	return ""
}

// refTargets: the in-module functions a value operand stands for, with how it was taken.
func refTargets(v ssa.Value) []([2]string) {
	var res [][2]string
	switch v := v.(type) {
	case *ssa.Function:
		if !inMod(v) || v.Parent() != nil {
			return nil
		}
		how := "func"
		if strings.HasSuffix(v.Name(), "$thunk") {
			how = "thunk"
		}
		if o := objOf(v); o != nil && isAbstract(o) {
			return ifaceRefs(o, how)
		}
		res = append(res, [2]string{key(v), how})
	case *ssa.MakeClosure:
		fn := v.Fn.(*ssa.Function)
		if fn.Parent() != nil || !inMod(fn) { // an anonymous closure folds into its encloser
			return nil
		}
		how := "bound"
		if o := objOf(fn); o != nil && isAbstract(o) {
			return ifaceRefs(o, "bound")
		}
		res = append(res, [2]string{key(fn), how})
	}
	return res
}

func isAbstract(o *types.Func) bool {
	r := o.Type().(*types.Signature).Recv()
	return r != nil && types.IsInterface(r.Type())
}

// ifaceRefs: a method value on an interface stands for that method of every in-module type
// that implements the interface.
func ifaceRefs(o *types.Func, how string) [][2]string {
	it := o.Type().(*types.Signature).Recv().Type()
	iface, _ := it.Underlying().(*types.Interface)
	if iface == nil {
		return nil
	}
	name, _ := ifaceScope(it)
	var res [][2]string
	for _, n := range modTypes {
		var t types.Type = n
		if !types.Implements(t, iface) {
			t = types.NewPointer(n)
			if !types.Implements(t, iface) {
				continue
			}
		}
		obj, _, _ := types.LookupFieldOrMethod(t, true, o.Pkg(), o.Name())
		if m, ok := obj.(*types.Func); ok && m.Pkg() != nil && inModPath(m.Pkg().Path()) {
			res = append(res, [2]string{funcKey(m.Origin()), "iface:" + name + "/" + how})
		}
	}
	return res
}

// loadStandard is the gold loader: go/packages LoadAllSyntax (cgo processed by the real
// toolchain), every package's bodies built.
func loadStandard() (*ssa.Program, []*types.Package) {
	cfg := &packages.Config{Mode: packages.LoadAllSyntax, Dir: modDir, Tests: false, Env: os.Environ()}
	pkgs, err := packages.Load(cfg, "./...")
	if err != nil {
		panic(err)
	}
	bad := 0
	packages.Visit(pkgs, nil, func(p *packages.Package) {
		for _, e := range p.Errors {
			fmt.Fprintln(os.Stderr, "LOAD-ERROR", p.PkgPath, e)
			if inModPath(p.PkgPath) {
				bad++
			}
		}
		if p.IllTyped && inModPath(p.PkgPath) {
			fmt.Fprintln(os.Stderr, "ILL-TYPED in-module package", p.PkgPath)
			bad++
		}
	})
	if bad > 0 {
		fmt.Fprintln(os.Stderr, "in-module load errors:", bad)
		os.Exit(1)
	}
	prog, _ := ssautil.AllPackages(pkgs, ssa.InstantiateGenerics)
	prog.Build()
	var mod []*types.Package
	for _, p := range pkgs {
		if inModPath(p.PkgPath) {
			mod = append(mod, p.Types)
		}
	}
	return prog, mod
}

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage: exp4cg <moduleDir> <modulePath> [-fakecgo]")
		os.Exit(2)
	}
	modDir, modPath = os.Args[1], os.Args[2]
	modDir, _ = filepath.Abs(modDir)
	goos, goarch := os.Getenv("GOOS"), os.Getenv("GOARCH")
	if goos == "" || goarch == "" {
		fmt.Fprintln(os.Stderr, "set GOOS and GOARCH explicitly")
		os.Exit(2)
	}
	var prog *ssa.Program
	var modPkgs []*types.Package
	if len(os.Args) > 3 && os.Args[3] == "-fakecgo" {
		prog, modPkgs = loadFakeCgo(goarch)
	} else {
		prog, modPkgs = loadStandard()
	}
	fset = prog.Fset
	for _, p := range modPkgs {
		sc := p.Scope()
		for _, nm := range sc.Names() {
			tn, ok := sc.Lookup(nm).(*types.TypeName)
			if !ok || tn.IsAlias() {
				continue
			}
			if n, ok := tn.Type().(*types.Named); ok && !types.IsInterface(n) && n.TypeParams().Len() == 0 {
				modTypes = append(modTypes, n)
			}
		}
	}
	all := ssautil.AllFunctions(prog)
	emit("H\t%s\t%s\t%s", goos, goarch, modPath)

	for f := range all {
		if !inMod(f) {
			continue
		}
		t := top(f)
		k := key(f)
		{ // every encounter of k emits the same line: position and flags come from the declaration
			o := objOf(t)
			var file string
			var line int
			if o != nil && !isWrapper(t) {
				file, line = posOf(o.Pos())
			}
			if file == "" {
				file, line = posOf(t.Pos())
			}
			if file == "" && o != nil {
				file, line = posOf(o.Pos())
			}
			var flags []string
			if isWrapper(t) {
				flags = append(flags, "synth")
			}
			if t.Synthetic == "package initializer" {
				flags = append(flags, "pkginit")
			}
			if o != nil && o.Type().(*types.Signature).TypeParams().Len() > 0 {
				flags = append(flags, "generic")
			}
			if strings.HasSuffix(file, "_test.go") {
				flags = append(flags, "testfile")
			}
			pkgName := ""
			if o != nil && o.Pkg() != nil {
				pkgName = o.Pkg().Name()
			} else if t.Pkg != nil {
				pkgName = t.Pkg.Pkg.Name()
			}
			if pkgName == "main" {
				flags = append(flags, "main")
			}
			emit("F\t%s\t%s\t%d\t%s", k, file, line, strings.Join(flags, ","))
		}
		// function values taken inside f
		for _, b := range f.Blocks {
			for _, ins := range b.Instrs {
				var ops []*ssa.Value
				ops = ins.Operands(ops)
				call, isCall := ins.(ssa.CallInstruction)
				for i, op := range ops {
					if op == nil || *op == nil {
						continue
					}
					if isCall && i == 0 && call.Common().Value == *op {
						if _, ok := (*op).(*ssa.Function); ok {
							continue // the callee of a static call
						}
						if mc, ok := (*op).(*ssa.MakeClosure); ok && mc.Fn.(*ssa.Function).Parent() != nil {
							continue
						}
					}
					for _, rt := range refTargets(*op) {
						if rt[0] == k {
							continue
						}
						if isCall {
							launcher := isStdLauncher(call.Common())
							argIdx := -1
							for j, a := range call.Common().Args {
								if a == *op {
									argIdx = j
								}
							}
							if launcher == "" && argIdx >= 0 && launcherParam(call.Common().StaticCallee(), argIdx) {
								launcher = key(call.Common().StaticCallee())
							}
							if launcher != "" && argIdx >= 0 {
								emit("A\t%s\t%s\t%s\t%s", k, rt[0], siteIn(ins.Pos(), f), launcher)
								continue
							}
						}
						emit("R\t%s\t%s\t%s\t%s", k, rt[0], siteIn(ins.Pos(), f), rt[1])
					}
				}
			}
		}
	}

	dump := func(name string, cg *callgraph.Graph) {
		for f, n := range cg.Nodes {
			if f == nil || !inMod(f) {
				continue
			}
			for _, e := range n.In {
				if e.Caller == nil || e.Caller.Func == nil || e.Site == nil || !inMod(e.Caller.Func) {
					continue
				}
				a, b := key(e.Caller.Func), key(f)
				k := kindOf(e.Site)
				s := site(e.Site.Pos())
				if a == b && !isWrapper(f) {
					continue
				}
				emit("E\t%s\t%s\t%s\t%s\t%s", name, a, b, k, s)
				if k == "invoke" {
					in, sc := ifaceScope(e.Site.Common().Value.Type())
					emit("I\t%s\t%s\t%s\t%s\t%s\t%s", name, a, b, in, sc, s)
				}
			}
		}
	}
	chaG := cha.CallGraph(prog)
	dump("cha", chaG)
	vtaG := vta.CallGraph(all, chaG)
	dump("vta", vtaG)

	sort.Strings(out)
	var prev string
	for _, l := range out {
		if l != prev {
			fmt.Println(l)
		}
		prev = l
	}
}
