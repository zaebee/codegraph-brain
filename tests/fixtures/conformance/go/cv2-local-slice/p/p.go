package p

type Session struct{}

func (s *Session) stdin()  {}
func (s *Session) stdout() {}

func (s *Session) start() {
	type F func(*Session)
	for _, setupFd := range []F{(*Session).stdin, (*Session).stdout} {
		setupFd(s)
	}
}
