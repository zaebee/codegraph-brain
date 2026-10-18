package p

func measure(name string, fn func()) {
	fn()
}

func work() {}

func Run() {
	measure("work", work)
}
