package server

import "testing"

func TestStart(t *testing.T) {
	s := &Server{}
	s.Start()
	t.Log("ok")
}
