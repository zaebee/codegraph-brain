package p

import "example.com/m/q"

func run(t *q.T) {
	for _, f := range []func(*q.T){(*q.T).M} {
		f(t)
	}
}
