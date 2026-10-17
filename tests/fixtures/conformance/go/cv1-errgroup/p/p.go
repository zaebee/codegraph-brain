package p

import "golang.org/x/sync/errgroup"

type T struct{}

func (t *T) fetch() error { return nil }

func (t *T) run() error {
	var g errgroup.Group
	g.Go(t.fetch)
	return g.Wait()
}
