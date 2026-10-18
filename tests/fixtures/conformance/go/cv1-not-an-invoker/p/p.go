package p

type Pool struct{}

// Do keeps f for later; it is not sync.Once.Do, and the profile does not list it.
func (p *Pool) Do(f func()) {}

func work() {}

func run(p *Pool) { p.Do(work) }
