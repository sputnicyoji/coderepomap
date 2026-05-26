package service

type Runner interface {
	Run() error
}

type Service struct {
	Name string
	Result
}

type GenericBox[T any] struct {
	Value T
}

func NewService() *Service {
	return &Service{}
}

func (s *Service) Run() error {
	return nil
}

func (s Service) Describe() string {
	return s.Name
}
