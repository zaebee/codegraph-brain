package p

import "slices"

type Name string

func (n Name) Matches(child string) bool { return child == string(n) }

func keyZones(raw string, zones []string) bool {
	kname := Name(raw)
	return slices.ContainsFunc(zones, kname.Matches)
}
