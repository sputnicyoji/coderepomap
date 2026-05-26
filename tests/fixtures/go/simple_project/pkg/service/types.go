package service

type Result struct {
	Code int
}

func (r Result) Format() string {
	return ""
}
