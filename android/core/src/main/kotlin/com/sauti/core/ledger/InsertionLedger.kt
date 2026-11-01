package com.sauti.core.ledger

import java.io.File
import java.io.IOException
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** Where the ledger keeps its ids. The app supplies a SharedPreferences-backed one. */
interface LedgerStorage {
    /** Ids in insertion order (oldest first). Return an empty list if nothing is stored. */
    fun load(): List<String>

    /** Replace the stored ids with [ids] (oldest first). */
    fun save(ids: List<String>)
}

class InMemoryLedgerStorage : LedgerStorage {
    @Volatile
    private var ids: List<String> = emptyList()
    override fun load(): List<String> = ids
    override fun save(ids: List<String>) {
        this.ids = ids.toList()
    }
}

/**
 * One id per line in a UTF-8 text file, replaced atomically (temp file + move) on each save.
 */
class FileLedgerStorage(private val file: File) : LedgerStorage {

    override fun load(): List<String> {
        if (!file.isFile) return emptyList()
        return try {
            file.readLines(Charsets.UTF_8).map { it.trim() }.filter { it.isNotEmpty() }
        } catch (e: IOException) {
            emptyList()
        }
    }

    override fun save(ids: List<String>) {
        val dir = file.absoluteFile.parentFile
        if (dir != null && !dir.isDirectory) dir.mkdirs()
        val tmp = File(dir, file.name + ".tmp")
        tmp.writeText(ids.joinToString(separator = "\n", postfix = if (ids.isEmpty()) "" else "\n"), Charsets.UTF_8)
        try {
            Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        } catch (e: AtomicMoveNotSupportedException) {
            Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING)
        }
    }
}

/**
 * The ids of the last [capacity] utterances whose text was handed over for insertion
 * (docs/protocol.md, section 3.4 rule 3). A `final` whose id is already recorded must not be
 * inserted again; replayed or duplicated finals are therefore harmless.
 *
 * Thread-safe. Every change is written through [storage]; a storage failure is reported to
 * [onStorageError] and the ledger keeps working from memory.
 */
class InsertionLedger(
    private val storage: LedgerStorage,
    private val capacity: Int = DEFAULT_CAPACITY,
    private val onStorageError: (Throwable) -> Unit = {},
) {
    private val ids = LinkedHashSet<String>()

    init {
        require(capacity > 0) { "capacity must be positive" }
        val loaded = try {
            storage.load()
        } catch (e: Exception) {
            onStorageError(e)
            emptyList()
        }
        loaded.takeLast(capacity).forEach { ids.add(it) }
    }

    @Synchronized
    fun contains(utteranceId: String): Boolean = utteranceId in ids

    /**
     * Records [utteranceId]. Returns true if it was new (the caller may insert the text) and
     * false if it was already present (the caller must not insert it again).
     */
    @Synchronized
    fun record(utteranceId: String): Boolean {
        if (!ids.add(utteranceId)) return false
        while (ids.size > capacity) {
            val it = ids.iterator()
            it.next()
            it.remove()
        }
        try {
            storage.save(ids.toList())
        } catch (e: Exception) {
            onStorageError(e)
        }
        return true
    }

    @Synchronized
    fun size(): Int = ids.size

    @Synchronized
    fun snapshot(): List<String> = ids.toList()

    companion object {
        const val DEFAULT_CAPACITY: Int = 500
    }
}
