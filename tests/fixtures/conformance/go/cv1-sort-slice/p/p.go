package p

import "sort"

func byName(i, j int) bool { return i < j }

func order(xs []int) { sort.Slice(xs, func(i, j int) bool { return byName(i, j) }) }

func less(i, j int) bool { return i < j }

func orderBy(xs []int) { sort.Search(len(xs), func(i int) bool { return less(i, 0) }) }
