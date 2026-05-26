package handler

import (
	"example.com/myapp/pkg/service"
)

type Handler struct {
	svc *service.Service
}

func NewHandler() *Handler {
	return &Handler{}
}

func (h *Handler) Handle(s *service.Service) {
	s.Run()
}
