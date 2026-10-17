package p

type A struct{}

func (a *A) waitForRequirement()    {}
func (a *A) waitForWinRequirement() {}

func (a *A) wait(win bool) {
	fn := a.waitForRequirement
	if win {
		fn = a.waitForWinRequirement
	}
	fn()
}
