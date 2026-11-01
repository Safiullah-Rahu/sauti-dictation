package com.sauti.core

import com.sauti.core.ledger.FileLedgerStorage
import com.sauti.core.ledger.InMemoryLedgerStorage
import com.sauti.core.ledger.InsertionLedger
import com.sauti.core.ledger.LedgerStorage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.IOException

class InsertionLedgerTest {

    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun record_returnsFalseForADuplicate() {
        val ledger = InsertionLedger(InMemoryLedgerStorage())
        assertTrue(ledger.record("a"))
        assertFalse(ledger.record("a"))
        assertTrue(ledger.contains("a"))
        assertFalse(ledger.contains("b"))
    }

    @Test
    fun keepsOnlyTheLast500_evictingTheOldest() {
        val ledger = InsertionLedger(InMemoryLedgerStorage())
        for (i in 1..520) ledger.record("id-$i")
        assertEquals(500, ledger.size())
        assertFalse(ledger.contains("id-20"))
        assertTrue(ledger.contains("id-21"))
        assertTrue(ledger.contains("id-520"))
        assertEquals("id-21", ledger.snapshot().first())
    }

    @Test
    fun fileStorage_survivesARestart() {
        val file = tmp.root.resolve("sub/ledger.txt")
        val first = InsertionLedger(FileLedgerStorage(file))
        first.record("u1")
        first.record("u2")
        val second = InsertionLedger(FileLedgerStorage(file))
        assertTrue(second.contains("u1"))
        assertTrue(second.contains("u2"))
        assertFalse(second.record("u2"))
        assertEquals(listOf("u1", "u2"), FileLedgerStorage(file).load())
    }

    @Test
    fun fileStorage_trimsToCapacityOnLoad() {
        val file = tmp.newFile("big.txt")
        file.writeText((1..600).joinToString("\n") { "x$it" })
        val ledger = InsertionLedger(FileLedgerStorage(file))
        assertEquals(500, ledger.size())
        assertTrue(ledger.contains("x600"))
        assertFalse(ledger.contains("x100"))
    }

    @Test
    fun missingFile_isAnEmptyLedger() {
        val ledger = InsertionLedger(FileLedgerStorage(tmp.root.resolve("nope.txt")))
        assertEquals(0, ledger.size())
    }

    @Test
    fun storageFailure_isReported_andTheLedgerKeepsWorkingInMemory() {
        val errors = ArrayList<Throwable>()
        val broken = object : LedgerStorage {
            override fun load(): List<String> = throw IOException("cannot read")
            override fun save(ids: List<String>) = throw IOException("disk full")
        }
        val ledger = InsertionLedger(broken, onStorageError = { errors.add(it) })
        assertTrue(ledger.record("a"))
        assertFalse(ledger.record("a"))
        assertEquals(2, errors.size)
    }
}
