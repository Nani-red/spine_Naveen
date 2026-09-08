<?php

namespace App\Http;

use App\Http\Controllers\OrderController;

Route::get('/orders', [OrderController::class, 'index']);
Route::post('/orders', 'OrderController@store');
Route::any('/orders/anything', [OrderController::class, 'index']);
Route::get('/orders/closure', function () {
    return 1;
});

Route::prefix('/v1')->group(function () {
    Route::get('/orders', [OrderController::class, 'index']);
});
