<?php

namespace App\Svc;

use App\Svc\Support\Formatter;

interface Payable
{
    public function pay(): void;
}

trait Loggable
{
    public function log(): void
    {
    }
}

enum Status: string
{
    case Active = 'active';
}

class Invoice implements Payable
{
    use Loggable;

    public function __construct(
        private readonly Formatter $formatter,
    ) {
    }

    public function pay(): void
    {
        $this->log();
        self::helper();
        new Receipt();
    }

    private static function helper(): void
    {
    }
}

class Receipt
{
}
