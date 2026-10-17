package p

type Command struct{ RunE func() error }

func startAction() error { return nil }

func newStartCommand() *Command { return &Command{RunE: startAction} }
