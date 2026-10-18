package lg

type P struct{}

func (p P) Errorf(format string, args ...any) {}

func New(name string) P { return P{} }
