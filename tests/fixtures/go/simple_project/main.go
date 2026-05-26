package main

import (
	"example.com/myapp/pkg/handler"
	svc "example.com/myapp/pkg/service"
)

func main() {
	h := handler.NewHandler()
	s := svc.NewService()
	h.Handle(s)
}
