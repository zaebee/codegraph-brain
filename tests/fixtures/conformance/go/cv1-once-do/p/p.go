package p

import "sync"

type Client struct{ once sync.Once }

func (c *Client) handleForwards() {}

func (c *Client) ListenTCP() { c.once.Do(c.handleForwards) }
