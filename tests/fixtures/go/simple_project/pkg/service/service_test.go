package service

import "testing"

func TestRun(t *testing.T) {
	s := NewService()
	if err := s.Run(); err != nil {
		t.Fatal(err)
	}
}
