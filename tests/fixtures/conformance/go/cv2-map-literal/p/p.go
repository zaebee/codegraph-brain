package p

type T struct{}

func (t *T) mapped() {}

func (t *T) each() {
	for _, f := range map[string]func(){"a": t.mapped} {
		f()
	}
}
